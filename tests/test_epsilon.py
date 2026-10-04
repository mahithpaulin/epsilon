"""Epsilon v1 tests — syntax + exec + anti-hardcode. Run in Actions; light enough for phone."""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from epsilon import generate, verify_code
from epsilon import ranker


def _gen(spec, lang='python'):
    r = generate(spec, lang=lang)
    assert r['verdict']['syntax_ok'], (spec, lang, r['verdict'])
    return r['code'], r['ir']


def test_add_python():
    code, ir = _gen('function add(a, b) returns sum', 'python')
    ns = {}
    exec(compile(code, '<t>', 'exec'), ns)
    assert ns['add'](2, 3) == 5


def test_add_js_syntax():
    code, _ = _gen('function add(a, b) returns sum', 'js')
    assert 'function add' in code and 'return' in code


def test_factorial_python():
    code, ir = _gen('factorial function', 'python')
    ns = {}
    exec(compile(code, '<t>', 'exec'), ns)
    fn = ir['functions'][0]['name']
    assert ns[fn](0) == 1 and ns[fn](5) == 120


def test_fib_python():
    code, ir = _gen('fibonacci function', 'python')
    ns = {}
    exec(compile(code, '<t>', 'exec'), ns)
    fn = ir['functions'][0]['name']
    assert ns[fn](7) == 13


def test_fizzbuzz_python():
    code, ir = _gen('fizzbuzz', 'python')
    ns = {}
    exec(compile(code, '<t>', 'exec'), ns)
    fn = ir['functions'][0]['name']
    got = ns[fn](15)
    assert got[2] == 'Fizz' and got[4] == 'Buzz' and got[14] == 'FizzBuzz'


def test_prime_python():
    code, ir = _gen('prime check function', 'python')
    ns = {}
    exec(compile(code, '<t>', 'exec'), ns)
    fn = ir['functions'][0]['name']
    assert ns[fn](7) is True and ns[fn](8) is False and ns[fn](1) is False


def test_sort_real_codegen():
    code, ir = _gen('sort list xs', 'python')
    assert 'sorted(' not in code, 'must be real sort, not sorted()'
    ns = {}
    exec(compile(code, '<t>', 'exec'), ns)
    fn = ir['functions'][0]['name']
    assert ns[fn]([3, 1, 2]) == [1, 2, 3]


def test_reverse_vowels_even():
    for spec, args, want in [
        ('reverse string s', ['abc'], 'cba'),
        ('count vowels in string s', ['hello'], 2),
        ('check if n is even', [4], 'even'),
    ]:
        code, ir = _gen(spec, 'python')
        ns = {}
        exec(compile(code, '<t>', 'exec'), ns)
        fn = ir['functions'][0]['name']
        assert ns[fn](*args) == want, spec


def test_mutation_sensitivity():
    """Generativity: rename + const change must propagate (anti-hardcode)."""
    r1 = generate('function add(a, b) returns sum', 'python')
    r2 = generate('function my_add_xyz(a, b) returns sum', 'python')
    assert r1['code'] != r2['code']
    assert 'my_add_xyz' in r2['code'] and 'my_add_xyz' not in r1['code']


def test_no_pasted_programs():
    """Static anti-hardcode: no emitter holds a full-program literal."""
    root = os.path.join(os.path.dirname(__file__), '..', 'epsilon')
    for fn in os.listdir(root):
        if not fn.endswith('.py'):
            continue
        src = open(os.path.join(root, fn)).read()
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                s = node.value
                if s.count('\n') > 8 and 'def ' in s and 'return' in s:
                    raise AssertionError('pasted program in %s' % fn)


def test_ranker_tiny():
    assert ranker.PARAMS <= 1000
    ranker.ensure_trained()
    s = ranker.score(ranker.extract('def add(a, b):\n    return a + b\n'))
    assert 0.0 <= s <= 1.0


def test_all_bench_specs_syntax():
    from bench.bench import SPECS
    for spec in SPECS:
        for lang in ('python', 'js'):
            r = generate(spec, lang=lang)
            assert r['verdict']['syntax_ok'], (spec, lang, r['verdict'].get('errors'))
