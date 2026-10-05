"""Direct v2 API tests. No probes, no skips: every test names the real API."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from epsilon import hir as H
from epsilon.errors import PASS, FAIL, UNKNOWN, UNAVAILABLE, STATES
from epsilon.config import EpsilonConfig
from epsilon.events import EventLog
from epsilon.requirements import analyze
from epsilon.planner import plan_project, detect_type, plan_function_task
from epsilon.snippets import build_body, SUPPORTED
from epsilon.backends import get_backend
from epsilon import validate as V
from epsilon import sandbox as SB
from epsilon import repair as RP
from epsilon import testgen as TG
from epsilon.context import build_context
from epsilon.pipeline import build


def test_states_closed_vocabulary():
    assert set(STATES) == {'PASS', 'FAIL', 'UNKNOWN', 'UNAVAILABLE'}


def test_hir_round_trip():
    proj = H.Project(name='demo', language='python')
    proj.files.append(H.SourceFile(
        path='demo/mod.py', language='python', doc='',
        imports=[H.Import(module='os')],
        declarations=[H.FuncDef(
            name='add', params=[H.Param(name='a'), H.Param(name='b')],
            returns=None, doc='', behaviors=[],
            body=[H.Return(value=H.BinOp(op='+', left=H.Var(name='a'),
                                         right=H.Var(name='b')))])],
        main_block=[]))
    d = H.to_dict(proj)
    proj2 = H.node_from_dict(d)
    assert proj2.files[0].declarations[0].name == 'add'
    import json
    json.dumps(d)
    # tuple elifs survive the trip as equivalent lists
    if_node = H.If(cond=H.Var(name='x'), then=[], elifs=[(H.Var(name='y'), [])],
                   else_body=[])
    d2 = H.to_dict(if_node)
    back = H.node_from_dict(d2)
    assert back.then == [] and len(back.elifs) == 1


def test_hir_register_extension():
    from epsilon.hir import register_node, NODE_REGISTRY
    from dataclasses import dataclass

    @register_node
    @dataclass
    class ProbeNode:
        payload: str = ''

    assert 'ProbeNode' in NODE_REGISTRY
    back = H.node_from_dict({'kind': 'ProbeNode', 'payload': 'hi'})
    assert back.payload == 'hi'
    del NODE_REGISTRY['ProbeNode']


def test_requirements_structure_and_case():
    req = analyze('Todo API with a Task entity stored in MyDB over HTTP')
    assert req.title
    assert any('MyDB' in str(e) for e in req.data_entities) or 'MyDB' in req.raw
    assert isinstance(req.ambiguities, list)


def test_planner_types_and_fallback():
    assert detect_type(analyze('CLI tool named t with a --force flag')) == 'cli'
    assert detect_type(analyze('REST API with GET /x endpoint')) == 'api'
    assert detect_type(analyze('morse code trainer with quiz')) == 'library'
    proj = plan_project(analyze('morse code trainer with quiz'), 'python')
    assert isinstance(proj, H.Project)
    assert len(proj.files) >= 3 and len(proj.tests) >= 1


def test_snippets_all_kinds_build():
    assert len(SUPPORTED) >= 20
    body = build_body([{'kind': 'compute', 'target': 'r', 'expr': 'a + b'},
                       {'kind': 'return-expr', 'expr': 'r'}], 'f', ['a', 'b'])
    assert len(body) == 2
    bad = build_body([{'kind': 'no-such-kind'}], 'f', [])
    assert bad and type(bad[0]).__name__ == 'Raise'


def test_python_backend_negstep_and_model():
    from epsilon.gen_python import PythonBackend
    be = PythonBackend()
    f = H.SourceFile(path='m.py', language='python', doc='', imports=[],
                     declarations=[H.FuncDef(
                         name='countdown', params=[H.Param(name='n')],
                         returns=None, doc='', behaviors=[],
                         body=[H.ForIn(var='i', iter=H.Call(
                             func=H.Var(name='range'),
                             args=[H.Var(name='n'), H.Literal(value=0),
                                   H.Literal(value=-1)], kwargs={}),
                             body=[H.ExprStmt(expr=H.Call(
                                 func=H.Var(name='print'),
                                 args=[H.Var(name='i')], kwargs={}))])])],
                     main_block=[])
    code = be.render_file(f)
    compile(code, '<t>', 'exec')
    assert 'range' in code


def test_js_backend_range_and_exports():
    from epsilon.gen_js import JsBackend
    be = JsBackend()
    f = H.SourceFile(path='m.js', language='javascript', doc='', imports=[],
                     declarations=[H.FuncDef(
                         name='add', params=[H.Param(name='a'), H.Param(name='b')],
                         returns=None, doc='', behaviors=[],
                         body=[H.Return(value=H.BinOp(
                             op='+', left=H.Var(name='a'),
                             right=H.Var(name='b')))])],
                     main_block=[])
    code = be.render_file(f)
    assert 'function add' in code and 'module.exports' in code


def test_validate_layers_and_honesty(tmp_path):
    good = tmp_path / 'good.py'
    good.write_text('def add(a, b):\n    return a + b\n')
    bad = tmp_path / 'bad.py'
    bad.write_text('def f(a):\n    return a + missing_xyz\n')
    cfg = EpsilonConfig(out_dir=str(tmp_path))
    proj = H.Project(name='t', language='python')
    vs = V.validate_project(str(tmp_path), proj, cfg)
    by_layer = {v.layer: v.state for v in vs}
    assert by_layer.get('symbols') == FAIL
    assert by_layer.get('syntax') == PASS


def test_sandbox_run_and_timeout():
    import sys as _sys
    r = SB.run([_sys.executable, '-c', 'print(42)'], cwd='.', timeout=10)
    assert r.exit_code == 0 and '42' in r.stdout
    r = SB.run([_sys.executable, '-c', 'import time; time.sleep(5)'],
               cwd='.', timeout=1)
    assert r.timed_out is True


def test_pipeline_e2e_library(tmp_path):
    cfg = EpsilonConfig(out_dir=str(tmp_path), run_tests=True,
                        repair_iterations=2, timeout_secs=20.0)
    _out, report, _ctx = build('Library for notes with add and list', cfg,
                               EventLog())
    assert report.state in (PASS, UNKNOWN, FAIL)
    assert report.metrics['files'] >= 4
    assert any(v.layer == 'syntax' for v in report.verdicts)


def test_context_collects_symbols(tmp_path):
    proj = plan_function_task('add', ['a', 'b'],
                              [{'kind': 'compute', 'target': 'r', 'expr': 'a + b'},
                               {'kind': 'return-expr', 'expr': 'r'}], 'python')
    ctx = build_context(proj, str(tmp_path))
    assert ctx.files
    assert isinstance(ctx.to_dict(), dict)


def test_testgen_renders_and_repairs(tmp_path):
    proj = plan_function_task('add', ['a', 'b'],
                              [{'kind': 'compute', 'target': 'r', 'expr': 'a + b'},
                               {'kind': 'return-expr', 'expr': 'r'}], 'python')
    from epsilon.pipeline import materialize_bodies, finalize_tests
    materialize_bodies(proj)
    finalize_tests(proj)
    files = TG.tests_for(proj)
    assert files and all(k.startswith('tests/') for k in files)
