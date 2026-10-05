"""Epsilon on a standard LLM benchmark (BIG-bench / BIG-Bench Hard style).

Method (solver-generation, the standard code-engine paradigm, cf. HumanEval):
for each task Epsilon receives ONLY a precise function contract (spec text),
generates a solver with epsilon.generate(), and the solver runs on benchmark
cases. No hand-written solvers, no prompt-specific tuning.

Scope honesty: full BIG-bench is 200+ tasks including judgment, humor, and
translation tasks that are not formalizable as verified code tasks at all.
This file covers the 10 BBH-core tasks that ARE formalizable. Tasks whose
contract needs English parsing are included anyway (raw-string inputs) and
expected to fail: those failures map Epsilon's boundary, not a bug.

Provenance per task: 'sampled' = cases taken verbatim from the public
benchmark data (word_sorting + dyck from google/BIG-bench task.json,
boolean_expressions from suzgunmirac/BIG-Bench-Hard bbh/*.json);
'constructed-verified' = harness-authored micro-cases in the exact BBH
format whose answers were verified by independent computation (Python
eval/datetime) or hand-trace before being frozen here.

Exit code is ALWAYS 0: this is a measurement, not a gate. CI asserts
harness properties separately (tests/test_bigbench.py).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

TASKS = [
    {
        'bbh_task': 'word_sorting',
        'provenance': 'sampled (google/BIG-bench word_sorting/task.json)',
        'contract': 'sort list xs',
        'gap': 'in-vocabulary',
        'cases': [
            (['stick', 'gelatine'], ['gelatine', 'stick']),
            (['gawk', 'four'], ['four', 'gawk']),
            (['natchez', 'moonlit'], ['moonlit', 'natchez']),
            (['mermaid', 'dunham'], ['dunham', 'mermaid']),
            (['punic', 'deathward', 'pigeon'], ['deathward', 'pigeon', 'punic']),
        ],
    },
    {
        'bbh_task': 'dyck_languages',
        'provenance': 'sampled (google/BIG-bench dyck_languages/task.json)',
        'contract': 'predict next closing brackets for sequence s',
        'gap': 'needs-stack-algorithm',
        'cases': [
            ('{ [ [ [ { [ ] } ] ]', '] }'),
            ('( < [ ( )', '] >'),
            ('< < [ { } ] >', '>'),
            ('[ ( { < [ ( ) ] > }', ') ]'),
            ('{ [ ( ) ]', '}'),
        ],
    },
    {
        'bbh_task': 'boolean_expressions',
        'provenance': 'sampled (BIG-Bench-Hard bbh/boolean_expressions.json)',
        'contract': 'evaluate boolean expression s',
        'gap': 'needs-expression-eval',
        'cases': [
            ('not ( True ) and ( True )', False),
            ('True and not not ( not False )', True),
            ('not True or False or ( False )', False),
            ('False or not ( True ) and False', False),
            ('True or not False and True and False', True),
        ],
    },
    {
        'bbh_task': 'multistep_arithmetic_two',
        'provenance': 'constructed-verified (answers by Python eval)',
        'contract': 'evaluate arithmetic expression s',
        'gap': 'needs-expression-eval',
        'cases': [
            ('3 + 4 * 2', 11),
            ('( 6 + 4 ) * 3', 30),
            ('20 - 4 * 3', 8),
            ('2 * 3 + 4 * 5', 26),
            ('100 - 10 * 9 + 5', 15),
        ],
    },
    {
        'bbh_task': 'object_counting',
        'provenance': 'constructed-verified (counts fixed by construction)',
        'contract': 'answer counting question s',
        'gap': 'needs-NL-parse',
        'cases': [
            ('I have 3 apples and 2 oranges. How many fruits do I have?', 5),
            ('A box contains 4 red balls and 6 blue balls. How many balls are in the box?', 10),
            ('There are 5 cats, 3 dogs, and 2 birds. How many animals are there?', 10),
            ('She bought 7 pens and lost 2 pens. How many pens does she have?', 5),
            ('On the shelf are 1 clock, 4 books, and 5 cups. How many objects?', 10),
        ],
    },
    {
        'bbh_task': 'navigate',
        'provenance': 'constructed-verified (hand-traced, start facing north)',
        'contract': 'answer navigation question s',
        'gap': 'needs-NL-parse',
        'cases': [
            ('Turn right and walk 2 units. Turn right and walk 2 units. Turn right and walk 2 units. Turn right and walk 2 units. Are you back where you started?', True),
            ('Turn left and walk 1 unit. Turn left and walk 1 unit. Are you back where you started?', False),
            ('Walk 3 units forward. Turn around. Walk 3 units forward. Are you back where you started?', True),
            ('Turn right and walk 5 units. Are you back where you started?', False),
            ('Walk 1 unit. Turn right. Walk 1 unit. Turn right. Walk 1 unit. Turn right. Walk 1 unit. Are you back where you started?', True),
        ],
    },
    {
        'bbh_task': 'date_understanding',
        'provenance': 'constructed-verified (answers by datetime)',
        'contract': 'answer date question s',
        'gap': 'needs-NL-parse',
        'cases': [
            ('Today is January 3, 2020. What is the date 5 days later?', 'January 8, 2020'),
            ('Today is March 1, 2021. What was the date 1 day ago?', 'February 28, 2021'),
            ('Today is December 30, 2019. What is the date 3 days later?', 'January 2, 2020'),
            ('Today is February 27, 2020. What is the date 2 days later?', 'February 29, 2020'),
            ('Today is July 4, 2021. What was the date 7 days ago?', 'June 27, 2021'),
        ],
    },
    {
        'bbh_task': 'web_of_lies',
        'provenance': 'constructed-verified (hand-traced, paradox-free chains)',
        'contract': 'answer truthfulness question s',
        'gap': 'needs-NL-parse',
        'cases': [
            ('Alice says Bob always tells the truth. Bob says the sky is green. Does Alice tell the truth?', False),
            ('Carol says Dave always lies. Dave says 2 + 2 = 5. Does Carol tell the truth?', True),
            ('Erin says Frank always tells the truth. Frank says Erin always tells the truth. Erin says water is wet. Does Frank tell the truth?', True),
            ('Gina says Hank always lies. Hank says snow is white. Does Gina tell the truth?', False),
            ('Ivy says Jack always lies. Jack says the moon is made of cheese. Does Ivy tell the truth?', True),
        ],
    },
    {
        'bbh_task': 'tracking_shuffled_objects_three',
        'provenance': 'constructed-verified (hand-traced swaps)',
        'contract': 'answer swapping question s',
        'gap': 'needs-NL-parse',
        'cases': [
            ('Alice has a ball, Bob has a doll, Carol has a pen. Alice and Bob swap. Where is the ball?', 'Bob'),
            ('Alice has a ball, Bob has a doll, Carol has a pen. Alice and Bob swap. Then Bob and Carol swap. Where is the ball?', 'Carol'),
            ('Dave has a coin, Erin has a key, Frank has a marble. Dave and Frank swap. Where is the key?', 'Erin'),
            ('Dave has a coin, Erin has a key, Frank has a marble. Dave and Frank swap. Then Erin and Frank swap. Where is the key?', 'Frank'),
            ('Gina has red, Hank has green, Ivy has blue. Hank and Ivy swap twice. Where is green?', 'Hank'),
        ],
    },
    {
        'bbh_task': 'logical_deduction_three',
        'provenance': 'constructed-verified (hand-traced orderings)',
        'contract': 'answer ordering question s',
        'gap': 'needs-NL-parse',
        'cases': [
            ('Ann finished above Bea. Bea finished above Cal. Who finished last?', 'Cal'),
            ('Dan finished below Eli. Eli finished below Fay. Who finished first?', 'Fay'),
            ('Gus finished above Hal. Hal finished above Ivy. Who finished second?', 'Hal'),
            ('Amy finished below Beth. Beth finished below Cara. Who finished first?', 'Cara'),
            ('Tom finished above Uma. Vera finished below Uma. Who finished second?', 'Uma'),
        ],
    },
]


def run_task(task):
    from epsilon import generate
    try:
        r = generate(task['contract'])
    except Exception as e:
        return {'bbh_task': task['bbh_task'], 'syntax_ok': False,
                'pass': False, 'passed': 0, 'total': len(task['cases']),
                'error': 'gen: %s' % e, 'gap': task['gap'],
                'provenance': task['provenance']}
    code, verdict = r['code'], r['verdict']
    if not verdict.get('syntax_ok'):
        return {'bbh_task': task['bbh_task'], 'syntax_ok': False,
                'pass': False, 'passed': 0, 'total': len(task['cases']),
                'error': 'syntax: %s' % verdict.get('errors'),
                'gap': task['gap'], 'provenance': task['provenance']}
    fname = r['ir']['functions'][0]['name']
    ns = {}
    try:
        exec(compile(code, '<bb>', 'exec'), ns)
        fn = ns[fname]
    except Exception as e:
        return {'bbh_task': task['bbh_task'], 'syntax_ok': True,
                'pass': False, 'passed': 0, 'total': len(task['cases']),
                'error': 'load: %s' % e, 'gap': task['gap'],
                'provenance': task['provenance']}
    passed, first_err = 0, ''
    for given, want in task['cases']:
        try:
            got = fn(given)
        except Exception as e:
            if not first_err:
                first_err = '%s raised %s: %s' % (fname, type(e).__name__, e)
            continue
        if got == want:
            passed += 1
        elif not first_err:
            first_err = '%r -> %r want %r' % (given, got, want)
    return {'bbh_task': task['bbh_task'], 'syntax_ok': True,
            'pass': passed == len(task['cases']),
            'passed': passed, 'total': len(task['cases']),
            'error': first_err, 'gap': task['gap'],
            'provenance': task['provenance']}


def run_all():
    results = [run_task(t) for t in TASKS]
    passed = sum(1 for r in results if r['pass'])
    syntax = sum(1 for r in results if r['syntax_ok'])
    return {'passed': passed, 'total': len(results), 'syntax_ok': syntax,
            'results': results}


if __name__ == '__main__':
    import json
    res = run_all()
    print(json.dumps(res, indent=2))
