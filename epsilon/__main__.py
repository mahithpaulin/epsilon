"""Epsilon CLI — stdout is CODE ONLY. Diagnostics go to stderr."""
import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog='epsilon')
    ap.add_argument('--version', action='store_true')
    sub = ap.add_subparsers(dest='cmd')

    g = sub.add_parser('generate')
    g.add_argument('spec')
    g.add_argument('--lang', default='python')
    g.add_argument('--no-rank', action='store_true')
    g.add_argument('--emit-ir', action='store_true')

    c = sub.add_parser('check')
    c.add_argument('file')
    c.add_argument('--lang', default='auto')

    b = sub.add_parser('bench')
    b.add_argument('--lang', default='both')
    b.add_argument('--json', action='store_true')

    args = ap.parse_args(argv)
    if args.version:
        from . import __version__
        print(__version__)
        return 0
    if args.cmd == 'generate':
        from . import generate
        try:
            r = generate(args.spec, lang=args.lang, rank=not args.no_rank)
        except ValueError as e:
            print('epsilon: error E3001: unknown spec: %s' % e, file=sys.stderr)
            return 3
        if args.emit_ir:
            print(json.dumps(r['ir']), file=sys.stderr)
        # code only to stdout
        sys.stdout.write(r['code'])
        if not r['code'].endswith('\n'):
            sys.stdout.write('\n')
        if not r['verdict'].get('syntax_ok'):
            print('epsilon: error E2001: syntax check failed: %s' % r['verdict'].get('errors'), file=sys.stderr)
            return 2
        return 0
    if args.cmd == 'check':
        from . import verify_code
        lang = args.lang
        if lang == 'auto':
            lang = 'python' if args.file.endswith('.py') else 'js'
        with open(args.file) as f:
            code = f.read()
        v = verify_code(code, lang)
        if v.get('syntax_ok'):
            return 0
        print('epsilon: error E2001: syntax check failed: %s:%s' % (args.file, v.get('errors')), file=sys.stderr)
        return 2
    if args.cmd == 'bench':
        from bench.bench import run_all
        res = run_all(lang=args.lang)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            for r in res['results']:
                print('%s %-12s syntax=%s exec=%s' % (
                    'PASS' if r['ok'] else 'FAIL', r['name'], r['syntax_ok'], r['exec_ok']))
            print('%d/%d passed' % (res['passed'], res['total']))
        return 0 if res['passed'] == res['total'] else 1
    ap.print_help()
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
