"""2.5 tests: table-driven languages, SWE injectors, perf caches."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from epsilon import hir as H
from epsilon.lang_tables import BY_NAME
from epsilon.backends import get_backend, supported_languages


def _add_func():
    return H.FuncDef(
        name='add',
        params=[H.Param(name='a', type=H.TypeRef(name='int')),
                H.Param(name='b', type=H.TypeRef(name='int'))],
        returns=H.TypeRef(name='int'), doc='', behaviors=[],
        body=[H.Return(value=H.BinOp(op='+', left=H.Var(name='a'),
                                     right=H.Var(name='b')))])


def test_28_languages_registered():
    langs = supported_languages()
    assert len(langs) == 28, len(langs)
    assert len(BY_NAME) == 25


def test_all_tables_render_hello():
    fails = []
    for lang in sorted(BY_NAME):
        try:
            be = get_backend(lang)
            src = H.SourceFile(path='m' + be.file_extension(), language=lang,
                               doc='', imports=[], declarations=[_add_func()],
                               main_block=[])
            code = be.render_file(src)
            assert len(code.splitlines()) >= 1, 'empty render'
            assert 'add' in code.lower()
        except Exception as e:
            fails.append((lang, '%s: %s' % (type(e).__name__, e)))
    assert not fails, fails


def test_golden_hello_per_language():
    cases = {
        'go': 'func add(', 'rust': 'fn add(', 'ruby': 'def add(',
        'lua': 'function add(', 'julia': 'function add(',
        'kotlin': 'fun add(', 'swift': 'func add(',
        'elixir': 'def add(', 'haskell': 'add a b =',
        'cobol': 'ADD SECTION.', 'fortran': 'function add(',
        'ada': 'function add(', 'vb': 'Function add(',
        'perl': 'sub add {', 'php': 'function add(',
        'csharp': 'Program', 'java': 'class Main',
        'scala': 'def add(', 'dart': 'add(', 'r': 'add <- function(',
        'groovy': 'def add(', 'objc': 'add(',
        'matlab': 'function', 'c': 'int add(', 'cpp': 'int add(',
    }
    for lang, marker in cases.items():
        be = get_backend(lang)
        src = H.SourceFile(path='m' + be.file_extension(), language=lang,
                           doc='', imports=[], declarations=[_add_func()],
                           main_block=[])
        code = be.render_file(src)
        assert marker in code, (lang, code[:200])


def test_tiers_declared():
    from collections import Counter
    tiers = Counter(s.tier for s in BY_NAME.values())
    assert tiers['general'] == 20 and tiers['legacy'] == 5, tiers


def test_unknown_node_raises_with_kind():
    be = get_backend('go')
    bad = H.SourceFile(path='m.go', language='go', doc='', imports=[],
                       declarations=[H.FuncDef(
                           name='f', params=[], returns=None, doc='',
                           behaviors=[],
                           body=[H.ExprStmt(expr=H.SetLit(items=[]))])],
                       main_block=[])
    try:
        be.render_file(bad)
    except ValueError as e:
        assert 'set literal' in str(e).lower() or 'unsupported' in str(e).lower()
    else:
        raise AssertionError('expected ValueError for set literal in go')


def test_swe_injectors_minimal():
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                                     'bench'))
    import swe_mini
    assert len(swe_mini.TASKS) == 8
    assert len(swe_mini.DEFECT_ORDER) >= 8
    assert swe_mini._inject('off-by-one', '/nonexistent-xyz', 0) is False
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as fh:
        fh.write('def f():\n    return True\n')
        path = fh.name
    try:
        assert swe_mini._inject('wrong-return', path, 0) is True
        assert 'return False' in open(path).read()
    finally:
        os.unlink(path)


def test_caches_correct_and_bypassable():
    from epsilon import validate as V
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as fh:
        fh.write('x = 1\n')
        path = fh.name
    try:
        t1, e1 = V._cached_tree(path)
        assert t1 is not None and e1 is None
        with open(path, 'w') as fh:
            fh.write('def broken(:\n')
        t2, e2 = V._cached_tree(path)
        assert t2 is None and e2 is not None  # self-invalidated, not stale
        os.environ['EPSILON_NO_CACHE'] = '1'
        t3, e3 = V._cached_tree(path)
        assert t3 is None
    finally:
        os.environ.pop('EPSILON_NO_CACHE', None)
        os.unlink(path)
