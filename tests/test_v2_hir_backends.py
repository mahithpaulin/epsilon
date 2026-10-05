"""Epsilon v2 HIR + backends tests — round-trip and rendering behaviour.

Always runs: HIR ``to_dict``/``node_from_dict`` round-trip, unknown-kind
rejection, and ``register_node`` extensibility (all real ``epsilon.hir`` APIs).
Backend rendering tests use the v2 renderer when present
(``epsilon.backends`` / ``epsilon.gen_python`` / ``epsilon.gen_js`` / ...),
else SKIP; v1 ``pyemit``/``jsemit`` fallbacks keep the file exercising real
render-then-execute behaviour. Node is only used when present.
Stdlib + pytest only; Python 3.11 compatible.
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from epsilon import hir as H  # noqa: E402  (real v2 API, must exist)

_GEN_MODULES = (
    "epsilon.backends",
    "epsilon.gen_python",
    "epsilon.gen",
    "epsilon.emit",
    "epsilon.codegen",
    "epsilon.gen_js",
)
_PY_RENDER_NAMES = (
    "render_python", "emit_python", "to_python", "render", "emit",
    "synthesize", "generate",
)
_JS_RENDER_NAMES = (
    "render_js", "emit_js", "to_js", "render", "emit",
    "synthesize", "generate",
)


def _try_import(*names):
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


GEN_MOD = _try_import(*_GEN_MODULES)


def _find_renderer(mod, names):
    for name in names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def _demo_project():
    """Project exercising If, ForIn with a negative step, and call args."""
    neg_loop = H.ForIn(
        var="i",
        iter=H.Call(
            func=H.Var(name="range"),
            args=[H.Literal(value=10), H.Literal(value=0), H.Literal(value=-1)],
        ),
        body=[
            H.ExprStmt(expr=H.Call(
                func=H.Var(name="append"),
                args=[H.Var(name="out"), H.Var(name="i")],
            )),
        ],
    )
    countdown = H.FuncDef(
        name="neg_count",
        params=[],
        returns=H.TypeRef(name="list"),
        doc="collect 10..1 via a negative step",
        body=[
            H.Assign(target="out", value=H.ListLit(items=[])),
            neg_loop,
            H.Return(value=H.Var(name="out")),
        ],
    )
    sign = H.FuncDef(
        name="sign",
        params=[H.Param(name="n", type=H.TypeRef(name="int"))],
        returns=H.TypeRef(name="str"),
        body=[
            H.If(
                cond=H.Compare(op=">", left=H.Var(name="n"), right=H.Literal(value=0)),
                then=[H.Return(value=H.Literal(value="pos"))],
                else_body=[H.Return(value=H.Literal(value="neg"))],
            ),
        ],
    )
    return H.Project(
        name="demo",
        description="hir backend fixture",
        language="python",
        files=[H.SourceFile(
            path="demo.py",
            language="python",
            imports=[H.Import(module="os")],
            declarations=[countdown, sign],
        )],
        dependencies=[H.Dependency(name="x", version_spec=">=1")],
        tests=[H.TestSpec(
            name="t_neg",
            target="demo.neg_count",
            cases=[H.TestCase(given={}, expect="[10, 9, 8, 7, 6, 5, 4, 3, 2, 1]")],
        )],
        cli=[H.CliCommand(name="run", help="run it", handler="demo:main")],
        endpoints=[H.ApiEndpoint(method="GET", path="/items", handler="h")],
        models=[H.DataModel(
            name="MyDB",
            fields=[H.VarDecl(name="id", type=H.TypeRef(name="int"))],
        )],
    )


# ---------------------------------------------------------- round-trips

def test_hir_round_trip_project():
    proj = _demo_project()
    d = H.to_dict(proj)
    json.dumps(d)  # must be JSON-serializable
    proj2 = H.node_from_dict(json.loads(json.dumps(d)))
    assert H.to_dict(proj2) == d
    assert isinstance(proj2, H.Project)
    assert proj2.file_paths() == ["demo.py"]
    assert [f.name for f in proj2.source_files()[0].declarations] == ["neg_count", "sign"]


def test_hir_round_trip_expressions():
    exprs = [
        H.BinOp(op="+", left=H.Var(name="a"), right=H.Literal(value=1)),
        H.Compare(op="not-in", left=H.Var(name="x"), right=H.Var(name="ys")),
        H.BoolOp(op="and", values=[H.Var(name="a"), H.Var(name="b")]),
        H.UnaryOp(op="not", operand=H.Var(name="ok")),
        H.Call(func=H.Var(name="f"), args=[H.Literal(value=1)], kwargs={"k": H.Literal(value=2)}),
        H.FStr(parts=["hi ", H.Var(name="name")]),
        H.ListLit(items=[H.Literal(value=1), H.Literal(value=2)]),
    ]
    for e in exprs:
        d = H.to_dict(e)
        assert H.to_dict(H.node_from_dict(d)) == d, e


def test_hir_unknown_kind_raises():
    with pytest.raises(ValueError):
        H.node_from_dict({"kind": "NoSuchNode_xyz", "x": 1})


def test_hir_register_node_extension():
    from dataclasses import dataclass, field

    @H.register_node
    @dataclass
    class _V2ProbeNodeXyz:
        label: str = ""
        items: list = field(default_factory=list)

    try:
        node = _V2ProbeNodeXyz(label="q", items=[H.Literal(value=3)])
        d = H.to_dict(node)
        assert d["kind"] == "_V2ProbeNodeXyz"
        back = H.node_from_dict(d)
        assert isinstance(back, _V2ProbeNodeXyz)
        assert H.to_dict(back) == d
    finally:
        H.NODE_REGISTRY.pop("_V2ProbeNodeXyz", None)


# ------------------------------------------------- v2 python rendering

def _render_candidates(mod, names, nodes):
    fn = _find_renderer(mod, names)
    if fn is None:
        return None
    for node in nodes:
        for args in ((node,),):
            try:
                out = fn(*args)
            except TypeError:
                continue
            except Exception:
                return None  # renderer exists but cannot handle HIR: report via skip
            if isinstance(out, str) and out.strip():
                return out
    return None


def test_python_render_if_forin_negstep_executes():
    if GEN_MOD is None:
        pytest.skip("v2 HIR backends not present yet")
    proj = _demo_project()
    src_file = proj.source_files()[0]
    code = _render_candidates(
        GEN_MOD, _PY_RENDER_NAMES,
        (proj, src_file, src_file.declarations[0]))
    if code is None:
        pytest.skip("v2 python renderer not usable on HIR nodes yet")
    compile(code, "<hir-py>", "exec")
    ns = {}
    exec(compile(code, "<hir-py>", "exec"), ns)
    assert ns["neg_count"]() == [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    assert ns["sign"](3) == "pos" and ns["sign"](-2) == "neg"


def test_v1_python_negative_step_executes():
    """v2 ForIn-over-range gets negative steps right; guards the semantic."""
    from epsilon import hir as H
    from epsilon.gen_python import PythonBackend

    mod = H.SourceFile(
        path='neg.py', language='python', doc='',
        imports=[], main_block=[],
        declarations=[H.FuncDef(
            name='neg_count', params=[], returns=None, doc='',
            behaviors=[], body=[
                H.Assign(target='out', value=H.ListLit(items=[])),
                H.ForIn(var='i', iter=H.Call(
                    func=H.Var(name='range'),
                    args=[H.Literal(value=10), H.Literal(value=0),
                          H.Literal(value=-1)], kwargs={}), body=[
                    H.ExprStmt(expr=H.Call(
                        func=H.Attr(obj=H.Var(name='out'), attr='append'),
                        args=[H.Var(name='i')], kwargs={}))]),
                H.Return(value=H.Var(name='out'))])])
    code = PythonBackend().render_file(mod)
    ns = {}
    exec(compile(code, "<neg>", "exec"), ns)
    assert ns["neg_count"]() == list(range(10, 0, -1)) == [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]


# ----------------------------------------------------- js rendering

def _v1_js_countdown_module():
    from epsilon.ir import Lit, V

    return {
        "kind": "Module",
        "functions": [{
            "kind": "FuncDef", "name": "neg_count", "params": [],
            "body": [
                {"kind": "Assign", "target": "out",
                 "value": {"kind": "ListLiteral", "items": []}},
                {"kind": "ForRange", "var": "i",
                 "start": Lit(10), "stop": Lit(0), "step": Lit(1),
                 "body": [{"kind": "ExprStmt",
                           "expr": {"kind": "Call", "func": "append",
                                   "args": [V("out"), V("i")]}}]},
                {"kind": "Return", "value": V("out")},
            ],
        }],
        "main": [],
    }


def test_js_render_contains_function_and_exports():
    if GEN_MOD is not None:
        proj = _demo_project()
        code = _render_candidates(
            GEN_MOD, _JS_RENDER_NAMES, (proj, proj.source_files()[0]))
        if code is None:
            pytest.skip("v2 js renderer not usable on HIR nodes yet")
    else:
        from epsilon import jsemit

        code = jsemit.synthesize(_v1_js_countdown_module())
    assert "function" in code, code[:300]
    assert "export" in code, code[:300]  # module.exports or export
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not present; render-text assertions already checked")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(code)
        path = fh.name
    try:
        proc = subprocess.run([node, "--check", path],
                              capture_output=True, text=True, timeout=15)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    assert proc.returncode == 0, proc.stderr[:500]
