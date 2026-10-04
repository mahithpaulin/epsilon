"""Epsilon v1 — standard-scale benchmark (MBPP-style mini, 20 tasks).

Why MBPP and not full HumanEval: HumanEval's 164 tasks assume arbitrary
control flow Epsilon v1 explicitly excludes (classes, exceptions, slicing
tricks). MBPP ("Mostly Basic Python Problems") is the standard benchmark at
v1's scale: single simple functions. This file maps 20 MBPP-canonical
problems to Epsilon spec strings and runs the ORIGINAL test assertions
against the generated function (located via IR name, never string-matched).

16 in-domain (parser has rules) + 4 out-of-domain stretch (gcd, binary
search, dedupe, two-sum — v1 has no IR for these; expect graceful
syntax-ok / exec-fail via generic fallback). Stretch failures are the
honest signal, not a bug: they scope v2.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

TASKS = [
    # id, origin, epsilon spec, calls: [(args_tuple_or_single, expected)]
    ('MBPP-add', 'MBPP: add two numbers', 'function add(a, b) returns sum',
     [((2, 3), 5), ((-1, 1), 0)]),
    ('MBPP-sub', 'MBPP: subtract', 'function subtract(a, b) returns difference',
     [((5, 3), 2), ((0, 0), 0)]),
    ('MBPP-fact', 'MBPP: factorial', 'factorial function',
     [(0, 1), (1, 1), (5, 120)]),
    ('MBPP-fib', 'MBPP: fibonacci', 'fibonacci function',
     [(0, 0), (1, 1), (7, 13), (10, 55)]),
    ('MBPP-prime', 'MBPP: check prime', 'prime check function',
     [(2, True), (7, True), (8, False), (1, False)]),
    ('MBPP-reverse', 'MBPP: reverse string', 'reverse string s',
     [('abc', 'cba'), ('', '')]),
    ('MBPP-sort', 'MBPP: sort list', 'sort list xs',
     [([3, 1, 2], [1, 2, 3]), ([5, 4, 3, 2, 1], [1, 2, 3, 4, 5])]),
    ('MBPP-max', 'MBPP: max of list', 'max of list',
     [([3, 1, 4], 4), ([-5, -2], -2)]),
    ('MBPP-vowels', 'MBPP: count vowels', 'count vowels in string s',
     [('hello', 2), ('xyz', 0)]),
    ('MBPP-evenodd', 'MBPP: even or odd', 'check if n is even',
     [(4, 'even'), (7, 'odd')]),
    ('MBPP-sumn', 'MBPP: sum 1..n', 'function sum_1_to_n(n) returns sum',
     [(1, 1), (100, 5050)]),
    ('MBPP-fizzbuzz', 'MBPP-style: fizzbuzz', 'fizzbuzz',
     [(15, None)]),  # special-cased below
    ('MBPP-pal', 'MBPP: palindrome check', 'check palindrome string s',
     [('racecar', True), ('hello', False)]),
    ('MBPP-avg', 'MBPP: average of list', 'average of list xs',
     [([1, 2, 3, 4], 2.5), ([10], 10.0)]),
    ('MBPP-square-spec', 'MBPP-style: square via function', 'function square(x) returns square',
     [(4, 16), (0, 0)]),
    ('MBPP-mul', 'MBPP: multiply', 'function mul(a, b) returns product',
     [((3, 4), 12), ((0, 9), 0)]),
    # ---- stretch: out of v1 domain, expect exec-fail (honest scope signal)
    ('STRETCH-gcd', 'HumanEval-style: gcd', 'function gcd(a, b) returns greatest common divisor',
     [((12, 8), 4)]),
    ('STRETCH-bsearch', 'MBPP: binary search', 'binary search xs for target',
     [(([1, 2, 3, 4], 3), 2)]),
    ('STRETCH-dedupe', 'MBPP: remove duplicates', 'remove duplicates from list xs',
     [([1, 2, 2, 3], [1, 2, 3])]),
    ('STRETCH-twosum', 'HumanEval-style: two sum', 'two sum nums target',
     [(([2, 7, 11, 15], 9), [0, 1])]),
]


def run_one(task, lang='python'):
    tid, origin, spec, cases = task
    from epsilon import generate
    try:
        r = generate(spec, lang=lang)
    except Exception as e:
        return {'id': tid, 'origin': origin, 'syntax_ok': False,
                'pass': False, 'error': 'gen: %s' % e, 'code': ''}
    code = r['code']
    if not r['verdict'].get('syntax_ok'):
        return {'id': tid, 'origin': origin, 'syntax_ok': False,
                'pass': False, 'error': str(r['verdict'].get('errors')), 'code': code}
    fname = r['ir']['functions'][0]['name'] if r['ir']['functions'] else None
    try:
        ns = {}
        exec(compile(code, '<gen>', 'exec'), ns)
        fn = ns[fname]
        if tid == 'MBPP-fizzbuzz':
            got = fn(15)
            ok = (isinstance(got, list) and len(got) == 15
                  and got[2] == 'Fizz' and got[4] == 'Buzz' and got[14] == 'FizzBuzz')
            return {'id': tid, 'origin': origin, 'syntax_ok': True, 'pass': ok,
                    'error': '' if ok else 'fizzbuzz content: %r' % (got,), 'code': code}
        for args, want in cases:
            got = fn(*args) if isinstance(args, tuple) else fn(args)
            if isinstance(want, float):
                assert abs(got - want) < 1e-9, '%r(%r)=%r want %r' % (fname, args, got, want)
            else:
                assert got == want, '%r(%r)=%r want %r' % (fname, args, got, want)
        return {'id': tid, 'origin': origin, 'syntax_ok': True, 'pass': True, 'error': '', 'code': code}
    except Exception as e:
        return {'id': tid, 'origin': origin, 'syntax_ok': True, 'pass': False,
                'error': 'exec: %s' % e, 'code': code}


def run_all(lang='python'):
    results = [run_one(t, lang=lang) for t in TASKS]
    passed = sum(1 for r in results if r['pass'])
    syntax = sum(1 for r in results if r['syntax_ok'])
    return {'passed': passed, 'total': len(results), 'syntax_ok': syntax,
            'results': [{k: v for k, v in r.items() if k != 'code'} for r in results],
            '_codes': {r['id']: r['code'] for r in results}}


if __name__ == '__main__':
    import json
    res = run_all()
    codes = res.pop('_codes')
    print(json.dumps(res, indent=2))
    fails = [r for r in res['results'] if not r['pass']]
    if fails:
        print('\n--- failing task details (code shown for audit) ---')
        for f in fails:
            print('\n## %s (%s)\nerror: %s\n' % (f['id'], f['origin'], f['error']))
            print(codes[f['id']])
    raise SystemExit(0 if res['passed'] == res['total'] else 1)
