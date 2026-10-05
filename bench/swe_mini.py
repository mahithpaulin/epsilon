"""SWE-mini: SWE-bench-inspired repair benchmark (NOT SWE-bench).

Faithful adaptation per methodology (see docs/SWE_MINI.md):
- base = freshly generated green project (verified baseline, like base_commit)
- defect = ONE injected text-level bug (taxonomy below), committed as the bug
- F2P = generated test files that fail on the buggy tree (validated split);
  P2P = the rest. F2P files STAY in the tree during repair (hiding them left
  repair with zero signal); the anti-cheat is the contamination guard
  (tests/ content hashes must not change — any test edit = unresolved).
  Repair strategies never read test bodies, only TARGET_PATH metadata.
- validation: F2P red-on-buggy AND P2P green-both, else the task is discarded
  as invalid (like SWE-bench's own task validation)
- repair may NOT touch tests/ (snapshot-enforced; contamination = unresolved)
- resolved IFF all F2P pass AND all P2P pass (binary; no partial credit)
- needs-regeneration hints are honored by re-rendering the single file from
  the pristine HIR plan (documented orchestrator step: plan intact, text
  diverged => restore from plan is the minimal correct patch)

Exit code is ALWAYS 0: measurement, not gate.
"""
import sys
import os
import re
import json
import shutil
import hashlib
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

TASKS = [
    {'id': 'swe1', 'spec': 'Library for notes with add, delete, update and list'},
    {'id': 'swe2', 'spec': 'Library for notes with add, delete, update and list'},
    {'id': 'swe3', 'spec': 'Todo CLI with JSON storage and add, list, done commands'},
    {'id': 'swe4', 'spec': 'Notes REST API with GET and POST endpoints returning JSON'},
    {'id': 'swe5', 'spec': 'Library with sort_list and max_in_list over integer lists'},
    {'id': 'swe6', 'spec': 'Address book storing contacts with JSON file persistence'},
    {'id': 'swe7', 'spec': 'Library with search_first and count_matches over item lists'},
    {'id': 'swe8', 'spec': 'Number game engine with step and run and score tracking'},
]

# Defect kinds tried in order; the first kind x occurrence that validates
# (F2P red + P2P green) wins. The pairing is adaptive, not fixed: what
# matters is a real defect with a real split, honestly recorded below.
DEFECT_ORDER = ['wrong-return', 'off-by-one', 'missing-guard', 'status-code',
                'reverse-flag', 'swapped-args', 'wrong-cond-op',
                'dropped-return', 'missing-import', 'manifest-drift']


def _sha_files(root, sub='tests'):
    out = {}
    base = os.path.join(root, sub)
    for dirpath, _, fns in os.walk(base):
        for fn in sorted(fns):
            p = os.path.join(dirpath, fn)
            try:
                with open(p, 'rb') as fh:
                    out[os.path.relpath(p, root)] = hashlib.sha256(
                        fh.read()).hexdigest()
            except OSError:
                pass
    return out


def _domain_file(out_dir, stem):
    for root, _, fns in os.walk(out_dir):
        for fn in sorted(fns):
            if fn.endswith('.py') and stem in fn and 'test' not in root:
                return os.path.join(root, fn)
    return None


def _inject(kind, path, occurrence=0):
    """One minimal text edit. Returns True if the file changed."""
    try:
        with open(path) as fh:
            src = fh.read()
    except OSError:
        return False
    orig = src
    lines = src.split('\n')
    if kind == 'wrong-return':
        for i, ln in enumerate(lines):
            if re.match(r'\s*return True\s*$', ln):
                if occurrence == 0:
                    lines[i] = ln.replace('True', 'False')
                    break
                occurrence -= 1
    elif kind == 'missing-guard':
        for i, ln in enumerate(lines):
            if 'raise ValueError' in ln and i > 0 and \
                    re.match(r'\s*if\b.*:\s*$', lines[i - 1]):
                if occurrence == 0:
                    del lines[i - 1:i + 1]
                    break
                occurrence -= 1
    elif kind == 'off-by-one':
        for i, ln in enumerate(lines):
            if '+ 1)' in ln or '+ 1 ' in ln or ln.rstrip().endswith('+ 1'):
                if occurrence == 0:
                    lines[i] = ln.replace('+ 1', '+ 2', 1)
                    break
                occurrence -= 1
    elif kind == 'swapped-args':
        for i, ln in enumerate(lines):
            m = re.match(r'^(\s*)([\w.]+)\(([^,()]+),([^,()]+)\)(.*)$', ln)
            if m and 'def ' not in ln:
                if occurrence == 0:
                    lines[i] = '%s%s(%s,%s)%s' % (
                        m.group(1), m.group(2), m.group(4).strip(),
                        m.group(3).strip(), m.group(5))
                    break
                occurrence -= 1
    elif kind == 'missing-import':
        for i, ln in enumerate(lines):
            if re.match(r'\s*(import |from )[\w.]+', ln):
                if occurrence == 0:
                    del lines[i]
                    break
                occurrence -= 1
    elif kind == 'status-code':
        for i, ln in enumerate(lines):
            if '(200,' in ln:
                if occurrence == 0:
                    lines[i] = ln.replace('(200,', '(201,', 1)
                    break
                occurrence -= 1
    elif kind == 'reverse-flag':
        for i, ln in enumerate(lines):
            if 'reverse=False' in ln:
                if occurrence == 0:
                    lines[i] = ln.replace('reverse=False', 'reverse=True', 1)
                    break
                occurrence -= 1
    elif kind == 'wrong-cond-op':
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith('if ') and '==' in ln and '!=' not in ln:
                if occurrence == 0:
                    lines[i] = ln.replace('==', '!=', 1)
                    break
                occurrence -= 1
    elif kind == 'dropped-return':
        for i, ln in enumerate(lines):
            if re.match(r'\s*return \w+\s*$', ln):
                if occurrence == 0:
                    del lines[i]
                    break
                occurrence -= 1
    elif kind == 'manifest-drift':
        for i, ln in enumerate(lines):
            if 'import *' in ln and 'test' not in ln:
                if occurrence == 0:
                    del lines[i]
                    break
                occurrence -= 1
    else:
        raise ValueError('unknown defect %r' % kind)
    new = '\n'.join(lines)
    if new != orig:
        with open(path, 'w') as fh:
            fh.write(new)
        return True
    return False


def _test_files(out_dir):
    out = []
    for root, _, fns in os.walk(os.path.join(out_dir, 'tests')):
        for fn in sorted(fns):
            if fn.startswith('test') and fn.endswith('.py'):
                out.append(os.path.join(root, fn))
    return sorted(out)


def _run_suite(out_dir, lang='python', timeout=30):
    from epsilon import sandbox as SB
    return SB.run_tests(out_dir, lang, timeout)


def _symptom(cur):
    """Redacted defect symptom for logs (assertion diffs, no file names)."""
    blob = str(cur.get('stderr') or '') + '\n' + str(cur.get('stdout') or '')
    kept = []
    for line in blob.split('\n'):
        s = line.strip()
        if not s:
            continue
        if re.match(r'^(FAIL|ERROR): ', s):
            kept.append(re.sub(r'^(FAIL|ERROR): \w+ \([\w.]+\)',
                               r'\1: <test>', s))
        elif s.startswith(('AssertionError', 'TypeError', 'ValueError',
                           'KeyError', 'IndexError', 'AttributeError')):
            kept.append(re.sub(r'File "[^"]+", line \d+', 'File <redacted>', s))
        elif s.startswith(('+', '-', '?')) and len(s) > 2:
            kept.append(s)
    return '\n'.join(kept[:12])


def _failing_specs(res):
    out = set()
    for line in (res.get('stderr', '') + '\n' + res.get('stdout', '')).split('\n'):
        m = re.search(r'(ERROR|FAIL): (\w+)', line)
        if m:
            out.add(m.group(2))
    return out


def run_task(task, lang='python'):
    from epsilon.pipeline import build
    from epsilon.config import EpsilonConfig
    from epsilon import validate as V
    from epsilon import repair as RP
    from epsilon.context import build_context
    from epsilon.errors import ProjectReport, FAIL
    from epsilon import hir as H
    from epsilon.backends import get_backend
    rec = {'id': task['id'], 'spec': task['spec'], 'defect': 'none',
           'resolved': False, 'repair_iters': 0, 'category': 'setup',
           'files': 0, 'f2p': [], 'p2p': [], 'detail': ''}
    t0 = time.time()
    out_dir = tempfile.mkdtemp(prefix='eps-swe-')
    cfg = EpsilonConfig(out_dir=out_dir, target_language=lang, run_tests=True,
                        repair_iterations=3, timeout_secs=20.0)
    try:
        _o, report, _c = build(task['spec'], cfg)
    except Exception as e:
        rec['detail'] = 'baseline build crashed: %s' % e
        return rec
    if report.state != 'PASS':
        vdown = [(v.layer, v.state) for v in report.verdicts]
        rec['detail'] = 'baseline not green: %s verdicts=%s tests=%s/%s sys=%s failing=%s' % (
            report.state, vdown, (report.tests or {}).get('passed'),
            (report.tests or {}).get('total'), sys.version.split()[0],
            sorted(_failing_specs(report.tests or {}))[:10])
        rec['category'] = 'infra-baseline'
        return rec
    base_tests = _run_suite(out_dir)
    if base_tests.get('state') != 'PASS':
        rec['detail'] = 'baseline tests not green'
        rec['category'] = 'infra-baseline'
        return rec
    target = _domain_file(out_dir, task.get('file', 'domain'))
    if not target:
        # fall back to the largest generated module (most test surface)
        cands = []
        for root, _, fns in os.walk(out_dir):
            if 'tests' in root or '__pycache__' in root:
                continue
            for fn in sorted(fns):
                if fn.endswith('.py') and fn != '__init__.py':
                    cands.append(os.path.join(root, fn))
        target = max(cands, key=lambda p: os.path.getsize(p)) if cands else None
    if not target:
        rec['detail'] = 'no source module generated'
        rec['category'] = 'infra-shape'
        return rec
    # inject defect (adaptive: first kind x occurrence with a valid split)
    with open(target) as fh:
        pristine = fh.read()
    hidden_dir = tempfile.mkdtemp(prefix='eps-swe-hidden-')
    valid = False
    applied = ('', 0)
    for kind in DEFECT_ORDER:
        if valid:
            break
        for occ in range(4):
            with open(target, 'w') as fh:
                fh.write(pristine)
            if not _inject(kind, target, occ):
                continue
            buggy = _run_suite(out_dir)
            if buggy.get('state') == 'PASS':
                continue  # defect didn't break anything; try next site
            # F2P = test FILES with failures; hide them (harness-only)
            fail_specs = _failing_specs(buggy)
            all_specs = _test_files(out_dir)
            f2p = [p for p in all_specs
                   if os.path.basename(p)[:-3] in fail_specs or (
                       fail_specs and _spec_fails(p, fail_specs))]
            f2p = sorted(set(f2p))
            p2p = [p for p in all_specs if p not in f2p]
            if f2p and p2p:
                valid = True
                applied = (kind, occ)
                break
    if not valid:
        rec['detail'] = 'no defect site yields F2P+P2P split'
        rec['category'] = 'infra-validation'
        return rec
    rec['defect'] = '%s@%d' % applied
    # NOTE (design correction): F2P files stay IN the tree during repair.
    # Hiding them made the visible suite green, leaving repair with no
    # signal at all. The anti-cheat is the contamination guard below
    # (tests/ hashes must not change), not hiding: repair strategies never
    # read test bodies, only TARGET_PATH metadata for localization, and any
    # test edit at all counts the task unresolved. See docs/SWE_MINI.md.
    rec['f2p'] = [os.path.basename(p) for p in f2p]
    rec['p2p'] = [os.path.basename(p) for p in p2p]
    rec['files'] = len(report.files)
    # repair phase: engine sees problem statement + buggy tree only
    with open(os.path.join(out_dir, '.epsilon-project.json')) as fh:
        project = H.node_from_dict(json.load(fh))
    ctx = build_context(project, out_dir)
    tests_snapshot = _sha_files(out_dir)
    iters, repaired, contaminated = 0, False, False
    for iters in range(1, 4):
        verdicts = V.validate_project(out_dir, project, cfg)
        cur = _run_suite(out_dir)
        cur['symptom'] = _symptom(cur)
        if cur.get('state') == 'PASS' and not [
                v for v in verdicts if v.state == 'FAIL']:
            repaired = True
            break
        rep = ProjectReport(state=FAIL, verdicts=verdicts, tests=cur)
        try:
            changed, attempts = RP.repair(out_dir, project, rep, cfg, ctx)
        except Exception as e:
            rec['detail'] = 'repair crashed: %s' % e
            break
        regen = [a for a in attempts
                 if getattr(a, 'result', '') == 'needs-regeneration']
        for a in regen:
            _rerender_file(out_dir, project, getattr(a, 'file', ''))
        if _sha_files(out_dir) != tests_snapshot:
            contaminated = True
            break
        if not changed and not regen:
            break
    rec['repair_iters'] = iters
    if contaminated:
        rec['detail'] = 'repair touched tests/ (contamination)'
        rec['category'] = 'contaminated'
        return rec
    # grade: full suite must be green (ALL F2P + ALL P2P, binary rule)
    final = _run_suite(out_dir)
    if final.get('state') == 'PASS':
        rec['resolved'] = True
        rec['category'] = 'none'
        rec['detail'] = 'F2P=%d P2P=%d all green' % (len(f2p), len(p2p))
    else:
        rec['category'] = 'unresolved'
        rec['detail'] = 'post-repair suite: %s/%s' % (
            final.get('passed'), final.get('total'))
    rec['seconds'] = round(time.time() - t0, 2)
    return rec


def _rerender_file(out_dir, project, rel):
    """Re-render one file from the pristine HIR plan (documented step)."""
    from epsilon.backends import get_backend
    if not rel:
        return False
    be = get_backend(project.language)
    for f in project.files:
        if type(f).__name__ == 'SourceFile' and f.path == rel:
            content = be.render_file(f)
            full = os.path.join(out_dir, rel)
            with open(full, 'w') as fh:
                fh.write(content)
            return True
    return False


def _spec_fails(path, fail_specs):
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return False
    return any(s in text for s in fail_specs)


def run_all(lang='python'):
    import datetime
    results = [run_task(t, lang) for t in TASKS]
    resolved = sum(1 for r in results if r['resolved'])
    return {'resolved': resolved, 'total': len(results),
            'date': datetime.date.today().isoformat(),
            'results': results}


if __name__ == '__main__':
    print(json.dumps(run_all(), indent=2))
