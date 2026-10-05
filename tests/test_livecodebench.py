"""LiveCodeBench-mini harness properties. Green by design: asserts the pinned
measurement runs honestly, not that Epsilon passes contest problems."""
import importlib.util
import os


def _load():
    path = os.path.join(os.path.dirname(__file__), '..', 'bench',
                        'livecodebench_mini.py')
    spec = importlib.util.spec_from_file_location('livecodebench_mini', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pinned_version_matches_repo():
    import subprocess
    mod = _load()
    with open(os.path.join(os.path.dirname(__file__), '..', 'bench',
                           'EVAL_VERSION.json')) as fh:
        import json
        pinned = json.load(fh)
    head = subprocess.check_output(
        ['git', '-C', os.path.join(os.path.dirname(__file__), '..'),
         'rev-parse', 'HEAD'], text=True).strip()
    assert pinned['epsilon_commit'] == head, (pinned['epsilon_commit'], head)
    assert pinned['dataset'] == 'livecodebench/code_generation_lite'
    assert len(pinned['dataset_sha']) == 40


def test_six_real_problems_reported():
    mod = _load()
    res = mod.run_all()
    assert res['total'] == 6
    assert res['syntax_ok'] == 6
    assert {r['title'] for r in res['results']} >= {
        'A. Short Sort', 'B. Good Kid', 'D. 1D Eraser', 'B. Chemistry',
        'C. Raspberries', 'A. Game with Integers'}
    for r in res['results']:
        assert r['gap'] and r['total'] >= 4


def test_failures_show_distinct_mechanisms():
    mod = _load()
    res = mod.run_all()
    errs = ' '.join(r['error'] for r in res['results'])
    assert 'TypeError' in errs and ('want=' in errs or 'got=' in errs)
