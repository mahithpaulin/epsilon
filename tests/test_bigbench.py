"""BIG-bench-code harness properties. Green by design: asserts the measurement
runs and reports honestly, not that Epsilon passes LLM tasks."""
import importlib.util
import os


def _load():
    path = os.path.join(os.path.dirname(__file__), '..', 'bench', 'bigbench_code.py')
    spec = importlib.util.spec_from_file_location('bigbench_code', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_harness_covers_ten_formalizable_tasks():
    mod = _load()
    assert len(mod.TASKS) == 10
    assert {t['bbh_task'] for t in mod.TASKS} >= {
        'word_sorting', 'dyck_languages', 'boolean_expressions',
        'multistep_arithmetic_two', 'navigate', 'web_of_lies'}
    for t in mod.TASKS:
        assert t['contract'] and len(t['cases']) >= 5
        assert t['gap'] in ('in-vocabulary', 'needs-stack-algorithm',
                            'needs-expression-eval', 'needs-NL-parse')
        assert t['provenance']


def test_word_sorting_passes_on_real_data():
    mod = _load()
    res = mod.run_all()
    by_task = {r['bbh_task']: r for r in res['results']}
    assert by_task['word_sorting']['pass'] is True
    assert by_task['word_sorting']['passed'] == 5
    assert res['syntax_ok'] == res['total'] == 10


def test_failures_are_categorized_gaps():
    mod = _load()
    res = mod.run_all()
    fails = [r for r in res['results'] if not r['pass']]
    assert fails, 'expected boundary failures on an LLM benchmark'
    for r in fails:
        assert r['gap'] != 'in-vocabulary'
        assert r['error'], r['bbh_task']
