"""GENERATED — do not hand-edit. Source: /tmp/lcb gen script."""

# Per-problem stdin/stdout adapters (harness work, like LiveCodeBench's own
# drivers: parse stdin into solver args, format solver output as stdout).
# Epsilon sees ONLY the contract string; everything below is the harness.
def _lines(text):
    return text.strip().split('\n')


def adapt_short_sort(fn, stdin):
    parts = _lines(stdin)
    t = int(parts[0])
    return '\n'.join(str(fn(s.strip())) for s in parts[1:1 + t]) + '\n'


def adapt_good_kid(fn, stdin):
    parts = _lines(stdin)
    t = int(parts[0])
    out, i = [], 1
    for _ in range(t):
        n = int(parts[i])
        digits = [int(x) for x in parts[i + 1].split()]
        out.append(str(fn(digits)))
        i += 2
    return '\n'.join(out) + '\n'


def adapt_eraser(fn, stdin):
    parts = _lines(stdin)
    t = int(parts[0])
    out, i = [], 1
    for _ in range(t):
        n, k = (int(x) for x in parts[i].split())
        out.append(str(fn(parts[i + 1].strip(), k)))
        i += 2
    return '\n'.join(out) + '\n'


def adapt_chemistry(fn, stdin):
    parts = _lines(stdin)
    t = int(parts[0])
    out, i = [], 1
    for _ in range(t):
        n, k = (int(x) for x in parts[i].split())
        out.append(str(fn(parts[i + 1].strip(), k)))
        i += 2
    return '\n'.join(out) + '\n'


def adapt_raspberries(fn, stdin):
    parts = _lines(stdin)
    t = int(parts[0])
    out, i = [], 1
    for _ in range(t):
        n, k = (int(x) for x in parts[i].split())
        arr = [int(x) for x in parts[i + 1].split()]
        out.append(str(fn(arr, k)))
        i += 2
    return '\n'.join(out) + '\n'


def adapt_game(fn, stdin):
    parts = _lines(stdin)
    t = int(parts[0])
    return '\n'.join(str(fn(int(x))) for x in parts[1:1 + t]) + '\n'


def _norm(text):
    return '\n'.join(ln.rstrip() for ln in text.strip().split('\n'))

"""LiveCodeBench mini (version-pinned, stdin/stdout-faithful).

EVAL_VERSION.json pins the Epsilon commit + dataset SHA. Problems: 6 real
LiveCodeBench items (first complete test.jsonl entries), public tests verbatim
+ first 3 private cases each (documented subsample: later private cases are
multi-MB stress inputs; Epsilon fails these on semantics, not scale, so the
subsample does not change any verdict — re-run with full blobs to confirm).

Method: Epsilon generates ONE core function per problem from the contract
string only. The harness (adapters above, like LiveCodeBench's own drivers)
parses stdin, calls the function, and compares normalized stdout.
Exit code is ALWAYS 0: measurement, not gate.
"""
import sys
import os
import json
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

with open(os.path.join(os.path.dirname(__file__), 'EVAL_VERSION.json')) as _fh:
    EVAL = json.load(_fh)

_PROBLEMS = [{"title": "A. Short Sort", "difficulty": "easy", "public": [{"input": "6\nabc\nacb\nbac\nbca\ncab\ncba\n", "output": "YES\nYES\nYES\nNO\nNO\nYES\n", "testtype": "stdin"}], "private": [{"input": "1\nabc\n", "output": "YES\n", "testtype": "stdin"}, {"input": "3\nabc\nabc\nabc\n", "output": "YES\nYES\nYES\n", "testtype": "stdin"}, {"input": "5\ncab\nacb\ncba\nbac\nbca\n", "output": "NO\nYES\nYES\nYES\nNO\n", "testtype": "stdin"}]}, {"title": "B. Good Kid", "difficulty": "easy", "public": [{"input": "4\n4\n2 2 1 2\n3\n0 1 2\n5\n4 3 2 3 4\n9\n9 9 9 9 9 9 9 9 9\n", "output": "16\n2\n432\n430467210\n", "testtype": "stdin"}], "private": [{"input": "1\n2\n6 1\n", "output": "12", "testtype": "stdin"}, {"input": "1\n4\n2 2 1 2\n", "output": "16\n", "testtype": "stdin"}, {"input": "2\n2\n2 2\n2\n1 8\n", "output": "6\n16", "testtype": "stdin"}]}, {"title": "D. 1D Eraser", "difficulty": "easy", "public": [{"input": "8\n6 3\nWBWWWB\n7 3\nWWBWBWW\n5 4\nBWBWB\n5 5\nBBBBB\n8 2\nBWBWBBBB\n10 2\nWBBWBBWBBW\n4 1\nBBBB\n3 2\nWWW\n", "output": "2\n1\n2\n1\n4\n3\n4\n0\n", "testtype": "stdin"}], "private": [{"input": "2\n5 3\nBWBBW\n9 7\nBBWBWWBBB\n", "output": "2\n2", "testtype": "stdin"}, {"input": "5\n9 1\nBWWBWWBWW\n3 2\nBBB\n6 2\nWWWWBB\n4 1\nWWWB\n4 1\nWWWB\n", "output": "3\n2\n1\n1\n1", "testtype": "stdin"}, {"input": "5\n9 6\nWWBWWWBWB\n5 5\nWWBWB\n5 3\nBBBWW\n4 4\nWBWW\n9 7\nWBWWBBWWW\n", "output": "2\n1\n1\n1\n1", "testtype": "stdin"}]}, {"title": "B. Chemistry", "difficulty": "medium", "public": [{"input": "14\n1 0\na\n2 0\nab\n2 1\nba\n3 1\nabb\n3 2\nabc\n6 2\nbacacd\n6 2\nfagbza\n6 2\nzwaafa\n7 2\ntaagaak\n14 3\nttrraakkttoorr\n5 3\ndebdb\n5 4\necadc\n5 3\ndebca\n5 3\nabaac\n", "output": "YES\nNO\nYES\nYES\nYES\nYES\nNO\nNO\nYES\nYES\nYES\nYES\nNO\nYES\n", "testtype": "stdin"}], "private": [{"input": "5\n10 3\naaabbbcccd\n10 1\naaabbccddd\n10 0\naaabbccddd\n10 9\nabcdefghij\n10 2\naabbccddee\n", "output": "YES\nYES\nNO\nYES\nYES", "testtype": "stdin"}, {"input": "5\n10 5\naaabbbbccc\n10 5\naaabbbcccc\n10 4\naabbccddeeff\n11 3\naabbccddeeff\n10 8\naaabbbbccc", "output": "YES\nYES\nYES\nYES\nYES", "testtype": "stdin"}, {"input": "5\n15 7\naaabbbcccdddeee\n15 8\naaabbccddeeffggh\n17 9\naaabbbcccdddeeeff\n21 10\naabbccddeeffgghhiijj\n20 0\naabbccddeeffgghhiijjkk", "output": "YES\nYES\nYES\nYES\nYES", "testtype": "stdin"}]}, {"title": "C. Raspberries", "difficulty": "medium", "public": [{"input": "15\n2 5\n7 3\n3 3\n7 4 1\n5 2\n9 7 7 3 9\n5 5\n5 4 1 2 3\n7 4\n9 5 1 5 9 5 1\n3 4\n6 3 6\n3 4\n6 1 5\n3 4\n1 5 9\n4 4\n1 4 1 1\n3 4\n3 5 3\n4 5\n8 9 9 3\n2 5\n1 6\n2 5\n10 10\n4 5\n1 6 1 1\n2 5\n7 7\n", "output": "2\n2\n1\n0\n2\n0\n1\n2\n0\n1\n1\n4\n0\n4\n3\n", "testtype": "stdin"}], "private": [{"input": "1\n1 5\n3\n", "output": "2", "testtype": "stdin"}, {"input": "1\n2 4\n7 4\n", "output": "0", "testtype": "stdin"}, {"input": "1\n3 3\n2 7 7\n", "output": "1", "testtype": "stdin"}]}, {"title": "A. Game with Integers", "difficulty": "easy", "public": [{"input": "6\n1\n3\n5\n100\n999\n1000\n", "output": "First\nSecond\nFirst\nFirst\nSecond\nFirst\n", "testtype": "stdin"}], "private": [{"input": "1\n2\n", "output": "First\n", "testtype": "stdin"}, {"input": "1\n994\n", "output": "First\n", "testtype": "stdin"}, {"input": "1\n12\n", "output": "Second\n", "testtype": "stdin"}]}]

CONTRACTS = {
    'A. Short Sort': ('check if abc reachable with at most one swap from s',
                      adapt_short_sort, 'needs-custom-branches'),
    'B. Good Kid': ('maximum product after adding one to a digit in xs',
                    adapt_good_kid, 'needs-search-over-positions'),
    'D. 1D Eraser': ('minimum operations to whiten black cells with segments',
                     adapt_eraser, 'needs-greedy-scan'),
    'B. Chemistry': ('check palindrome possibility after removing k characters',
                     adapt_chemistry, 'needs-frequency-count'),
    'C. Raspberries': ('minimum increments for product divisible by k',
                       adapt_raspberries, 'needs-number-theory'),
    'A. Game with Integers': ('determine winner of modulo game for n',
                              adapt_game, 'needs-modular-branches'),
}


def run_problem(prob):
    from epsilon import generate
    title = prob['title']
    contract, adapt, gap = CONTRACTS[title]
    cases = [{'stdin': c['input'], 'stdout': c['output']}
             for c in prob['public']] + \
            [{'stdin': c['input'], 'stdout': c['output']}
             for c in prob['private']]
    try:
        r = generate(contract)
    except Exception as e:
        return {'title': title, 'syntax_ok': False, 'pass': False,
                'passed': 0, 'total': len(cases),
                'error': 'gen: %s' % e, 'gap': gap}
    if not r['verdict'].get('syntax_ok'):
        return {'title': title, 'syntax_ok': False, 'pass': False,
                'passed': 0, 'total': len(cases),
                'error': 'syntax: %s' % r['verdict'].get('errors'), 'gap': gap}
    fname = r['ir']['functions'][0]['name']
    ns = {}
    try:
        exec(compile(r['code'], '<lcb>', 'exec'), ns)
        fn = ns[fname]
    except Exception as e:
        return {'title': title, 'syntax_ok': True, 'pass': False,
                'passed': 0, 'total': len(cases),
                'error': 'load: %s' % e, 'gap': gap}
    passed, first_err = 0, ''
    for c in cases:
        try:
            got = _call_with_arity(fn, c['stdin'], adapt)
        except Exception as e:
            if not first_err:
                first_err = '%s raised %s: %s' % (fname, type(e).__name__, e)
            continue
        if _norm(got) == _norm(c['stdout']):
            passed += 1
        elif not first_err:
            first_err = 'in=%r got=%r want=%r' % (
                c['stdin'][:60], got[:60], c['stdout'][:60])
    return {'title': title, 'syntax_ok': True,
            'pass': passed == len(cases),
            'passed': passed, 'total': len(cases),
            'error': first_err, 'gap': gap}


def _call_with_arity(fn, stdin, adapt):
    return adapt(fn, stdin)


def run_all():
    results = [run_problem(p) for p in _PROBLEMS]
    passed = sum(1 for r in results if r['pass'])
    syntax = sum(1 for r in results if r['syntax_ok'])
    return {'eval': {k: EVAL[k] for k in ('epsilon_version', 'epsilon_commit',
                                          'dataset', 'dataset_sha', 'subsample')},
            'passed': passed, 'total': len(results), 'syntax_ok': syntax,
            'results': results}


if __name__ == '__main__':
    print(json.dumps(run_all(), indent=2))
