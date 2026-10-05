"""v1 API compatibility: same specs, same behavior, new machinery underneath."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from epsilon import generate, verify_code


def _exec(code, func, args):
    ns = {}
    exec(compile(code, '<compat>', 'exec'), ns)
    if isinstance(args, tuple):
        return ns[func](*args)
    return ns[func](args)


def _gen(spec, lang='python'):
    r = generate(spec, lang=lang)
    assert r['verdict']['syntax_ok'], (spec, lang, r['verdict'])
    return r['code'], r['ir']['functions'][0]['name']


def test_add():
    code, fn = _gen('function add(a, b) returns sum')
    assert _exec(code, fn, (2, 3)) == 5


def test_factorial():
    code, fn = _gen('factorial function')
    assert _exec(code, fn, 5) == 120
    assert _exec(code, fn, 0) == 1


def test_fibonacci():
    code, fn = _gen('fibonacci function')
    assert _exec(code, fn, 7) == 13


def test_fizzbuzz():
    code, fn = _gen('fizzbuzz')
    got = _exec(code, fn, 15)
    assert got[2] == 'Fizz' and got[4] == 'Buzz' and got[14] == 'FizzBuzz'


def test_prime():
    code, fn = _gen('prime check function')
    assert _exec(code, fn, 7) is True
    assert _exec(code, fn, 8) is False
    assert _exec(code, fn, 1) is False


def test_sort_max_reverse_vowels():
    code, fn = _gen('sort list xs')
    assert _exec(code, fn, [3, 1, 2]) == [1, 2, 3]
    code, fn = _gen('max of list')
    assert _exec(code, fn, [3, 1, 4]) == 4
    code, fn = _gen('reverse string s')
    assert _exec(code, fn, 'abc') == 'cba'
    code, fn = _gen('count vowels in string s')
    assert _exec(code, fn, 'hello') == 2


def test_even_sum():
    code, fn = _gen('check if n is even')
    assert _exec(code, fn, 4) == 'even'
    assert _exec(code, fn, 7) == 'odd'
    code, fn = _gen('function sum_1_to_n(n) returns sum')
    assert _exec(code, fn, 100) == 5050


def test_identifier_case_preserved():
    r = generate('function MyCounter(x) returns square')
    assert 'MyCounter' in r['code'], r['code']


def test_generic_fallback_honest():
    r = generate('function frobnicate_xyz(q) returns whatever')
    assert r['verdict']['syntax_ok']
    assert r['verdict']['candidates_considered'] == 1
    assert 'score = ' in r['verdict']['scoring']
    assert r['ir']['meta']['task'] == 'generic'


def test_verify_code_states():
    v = verify_code('def add(a, b):\n    return a + b\n')
    assert v['syntax_ok'] is True
    v = verify_code('def broken(:\n')
    assert v['syntax_ok'] is False


def test_js_syntax():
    r = generate('function add(a, b) returns sum', lang='js')
    assert r['verdict']['syntax_ok']
    assert 'function add' in r['code']
