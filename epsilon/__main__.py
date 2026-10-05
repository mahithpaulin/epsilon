"""Epsilon v2 CLI: generate | validate | test | repair | inspect | explain | build."""
from __future__ import annotations

import argparse
import json
import os
import sys


def _cfg(args) -> dict:
    from .config import EpsilonConfig
    cfg = EpsilonConfig(
        target_language=getattr(args, 'lang', 'python') or 'python',
        out_dir=getattr(args, 'out', 'dist') or 'dist',
        run_tests=not getattr(args, 'no_test', False),
        repair_iterations=getattr(args, 'max_iterations', 3) or 0,
        verbosity=getattr(args, 'verbose', 0) or 0,
        deterministic=not getattr(args, 'nondeterministic', False),
        timeout_secs=getattr(args, 'timeout', 30.0) or 30.0)
    problems = cfg.validate()
    if problems:
        print('epsilon: bad config: %s' % problems, file=sys.stderr)
        raise SystemExit(2)
    return cfg


def cmd_generate(args) -> int:
    from .pipeline import build
    from .events import EventLog
    events = EventLog()
    out_dir, report, _ctx = build(args.spec, _cfg(args), events)
    if args.verbose:
        for line in events.summary():
            print('# ' + line, file=sys.stderr)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.state in ('PASS', 'UNKNOWN') else 1


def cmd_single(args) -> int:
    """Legacy single-function output: code only on stdout (v1 behavior)."""
    from .compat_v1 import generate
    try:
        r = generate(args.spec, lang=args.lang)
    except ValueError as e:
        print('epsilon: error: %s' % e, file=sys.stderr)
        return 3
    sys.stdout.write(r['code'])
    if not r['code'].endswith('\n'):
        sys.stdout.write('\n')
    if not r['verdict'].get('syntax_ok'):
        print('epsilon: syntax check failed: %s' % r['verdict'].get('errors'),
              file=sys.stderr)
        return 2
    return 0


def _load_project(out_dir: str):
    path = os.path.join(out_dir, '.epsilon-project.json')
    if not os.path.isfile(path):
        return None
    from . import hir as H
    with open(path) as fh:
        return H.node_from_dict(json.load(fh))


def cmd_validate(args) -> int:
    from .pipeline import build as _  # noqa: ensure package importable
    from . import validate as V
    from .config import EpsilonConfig
    from .events import EventLog
    target = args.target
    if os.path.isdir(target):
        project = _load_project(target)
        cfg = EpsilonConfig(target_language=args.lang)
        verdicts = V.validate_project(target, project, cfg, EventLog())
    else:
        verdicts = V.validate_file(target, args.lang)
    ok = True
    for v in verdicts:
        print('%s %s' % (v.state, v.layer))
        for e in v.errors:
            print('  %s' % e)
            if getattr(e, 'severity', 'error') == 'error':
                ok = False
        if v.state == 'FAIL':
            ok = False
    return 0 if ok else 1


def cmd_test(args) -> int:
    from . import sandbox as SB
    res = SB.run_tests(args.target, args.lang, args.timeout or 30.0)
    print(json.dumps(res, indent=2))
    return 0 if res.get('state') == 'PASS' else 1


def cmd_repair(args) -> int:
    from . import validate as V
    from . import repair as RP
    from .config import EpsilonConfig
    from .context import build_context
    from .errors import ProjectReport
    from .events import EventLog
    out_dir = args.target
    project = _load_project(out_dir)
    if project is None:
        print('epsilon: no .epsilon-project.json in %s' % out_dir, file=sys.stderr)
        return 2
    cfg = EpsilonConfig(target_language=args.lang,
                        repair_iterations=args.max_iterations or 3,
                        max_repair_files=args.max_files or 5)
    events = EventLog()
    verdicts = V.validate_project(out_dir, project, cfg, events)
    report = ProjectReport(state='FAIL', verdicts=verdicts, tests={})
    ctx = build_context(project, out_dir)
    changed, attempts = RP.repair(out_dir, project, report, cfg, ctx, events)
    print(json.dumps({'changed': changed,
                      'attempts': [a.__dict__ if hasattr(a, '__dict__') else a
                                   for a in attempts]}, indent=2))
    return 0 if changed else 1


def cmd_inspect(args) -> int:
    from .context import build_context
    project = _load_project(args.target)
    if project is None:
        print('epsilon: no .epsilon-project.json in %s' % args.target, file=sys.stderr)
        return 2
    ctx = build_context(project, args.target)
    print(json.dumps(ctx.to_dict(), indent=2))
    return 0


def cmd_explain(args) -> int:
    project = _load_project(args.target)
    if project is None:
        print('epsilon: no .epsilon-project.json in %s' % args.target, file=sys.stderr)
        return 2
    print('# %s' % project.name)
    print('%s' % (project.description or ''))
    print('\nproject-type: %s' % (project.meta.get('project-type') if isinstance(project.meta, dict) else '?'))
    print('\n## decisions')
    meta = project.meta if isinstance(project.meta, dict) else {}
    for d in meta.get('decisions', []):
        print('- %s = %s (%s)' % (d.get('choice'), d.get('value'), d.get('reason', '')))
    print('\n## files')
    for f in project.files or []:
        print('- %s' % getattr(f, 'path', f))
    print('\n## entry: %s' % project.entry)
    return 0


def cmd_bench(args) -> int:
    import subprocess
    bench = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'bench', 'capability.py')
    if not os.path.isfile(bench):
        bench = 'bench/capability.py'
    cmd = [sys.executable, bench]
    if args.quick:
        cmd.append('--quick')
    if args.json:
        cmd.append('--json')
    return subprocess.call(cmd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='epsilon',
                                 description='Epsilon v2 general code engine')
    ap.add_argument('--version', action='store_true')
    sub = ap.add_subparsers(dest='cmd')

    g = sub.add_parser('generate', help='build a complete project from a spec')
    g.add_argument('spec')
    g.add_argument('--lang', default='python')
    g.add_argument('--out', default='dist')
    g.add_argument('--no-test', action='store_true')
    g.add_argument('--max-iterations', type=int, default=3)
    g.add_argument('--timeout', type=float, default=30.0)
    g.add_argument('--verbose', action='count', default=0)
    g.add_argument('--nondeterministic', action='store_true')

    s = sub.add_parser('single', help='legacy single-function output (code only)')
    s.add_argument('spec')
    s.add_argument('--lang', default='python')

    v = sub.add_parser('validate', help='validate a file or project directory')
    v.add_argument('target')
    v.add_argument('--lang', default='python')

    t = sub.add_parser('test', help='run a generated project test suite')
    t.add_argument('target')
    t.add_argument('--lang', default='python')
    t.add_argument('--timeout', type=float, default=30.0)

    r = sub.add_parser('repair', help='repair a generated project in place')
    r.add_argument('target')
    r.add_argument('--lang', default='python')
    r.add_argument('--max-iterations', type=int, default=3)
    r.add_argument('--max-files', type=int, default=5)

    i = sub.add_parser('inspect', help='dump project context as JSON')
    i.add_argument('target')

    e = sub.add_parser('explain', help='summarize plan and decisions')
    e.add_argument('target')

    b = sub.add_parser('bench', help='run the capability benchmark')
    b.add_argument('--quick', action='store_true')
    b.add_argument('--json', action='store_true')

    args = ap.parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0
    handlers = {'generate': cmd_generate, 'single': cmd_single,
                'validate': cmd_validate, 'test': cmd_test,
                'repair': cmd_repair, 'inspect': cmd_inspect,
                'explain': cmd_explain, 'bench': cmd_bench}
    if args.cmd in handlers:
        return handlers[args.cmd](args)
    ap.print_help()
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
