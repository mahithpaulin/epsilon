"""Epsilon 2.5 language matrix: render canonical HIR in all supported languages,
syntax-verify wherever a cheap checker exists. Measurement, exit 0 always.

Tiers: full (python/javascript/typescript: idiomatic backends), general
(17 table-driven), legacy (5 documented subsets) + haskell (expression-only).
UNKNOWN means 'no lightweight checker here' — never claimed as PASS.
"""
import sys
import os
import shutil
import subprocess
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

CHEAP_CHECKS = {
    # lang: (tool, argv with {file})
    'ruby': ('ruby', ['-c', '{file}']),
    'php': ('php', ['-l', '{file}']),
    'perl': ('perl', ['-c', '{file}']),
}
TRY_RUSTC = True  # best-effort, timeout-guarded; timeout => UNKNOWN


def _sample():
    from epsilon import hir as H
    add = H.FuncDef(
        name='add', params=[H.Param(name='a', type=H.TypeRef(name='int')),
                            H.Param(name='b', type=H.TypeRef(name='int'))],
        returns=H.TypeRef(name='int'), doc='', behaviors=[],
        body=[H.If(cond=H.Compare(op='==', left=H.Var(name='a'),
                                  right=H.Literal(value=0)),
                  then=[H.Return(value=H.Var(name='b'))],
                  elifs=[], else_body=[H.Return(value=H.BinOp(
                      op='+', left=H.Var(name='a'),
                      right=H.Var(name='b')))])])
    loop = H.FuncDef(
        name='countdown', params=[H.Param(name='n', type=H.TypeRef(name='int'))],
        returns=None, doc='', behaviors=[],
        body=[H.ForIn(var='i', iter=H.Call(
            func=H.Var(name='range'),
            args=[H.Var(name='n'), H.Literal(value=0),
                  H.Literal(value=-1)], kwargs={}), body=[
            H.ExprStmt(expr=H.Call(func=H.Var(name='print'),
                                   args=[H.Var(name='i')], kwargs={}))])])
    return add, loop


def _verify(lang, code, ext):
    if lang == 'python':
        try:
            compile(code, '<mx>', 'exec')
            return 'PASS', ''
        except SyntaxError as e:
            return 'FAIL', str(e)[:120]
    if lang == 'typescript':
        if shutil.which('tsc'):
            with tempfile.NamedTemporaryFile('w', suffix='.ts',
                                             delete=False) as fh:
                fh.write(code)
                path = fh.name
            try:
                r = subprocess.run(['tsc', '--noEmit', path],
                                   capture_output=True, text=True, timeout=30)
                msg = ((r.stdout or '') + '\n' + (r.stderr or '')).strip()[:300]
                return ('PASS', '') if r.returncode == 0 else (
                    'FAIL', msg)
            except subprocess.TimeoutExpired:
                return 'UNKNOWN', 'tsc timeout'
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        return 'UNAVAILABLE', 'tsc absent (node cannot parse type syntax)'
    if lang in ('javascript',) and shutil.which('node'):
        with tempfile.NamedTemporaryFile('w', suffix=ext,
                                         delete=False) as fh:
            fh.write(code)
            path = fh.name
        try:
            r = subprocess.run(['node', '--check', path], capture_output=True,
                               text=True, timeout=15)
            return ('PASS', '') if r.returncode == 0 else (
                'FAIL', (r.stderr or r.stdout)[:120])
        except subprocess.TimeoutExpired:
            return 'UNKNOWN', 'node --check timeout'
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    if lang in CHEAP_CHECKS:
        tool, argv = CHEAP_CHECKS[lang]
        if not shutil.which(tool):
            return 'UNKNOWN', 'no %s here' % tool
        with tempfile.NamedTemporaryFile('w', suffix=ext,
                                         delete=False) as fh:
            fh.write(code)
            path = fh.name
        try:
            r = subprocess.run([tool] + [a.replace('{file}', path)
                                         for a in argv],
                               capture_output=True, text=True, timeout=20)
            ok = r.returncode == 0
            return ('PASS', '') if ok else (
                'FAIL', (r.stderr or r.stdout)[:120])
        except subprocess.TimeoutExpired:
            return 'UNKNOWN', '%s timeout' % tool
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
    if lang == 'rust' and TRY_RUSTC and shutil.which('rustc'):
        with tempfile.NamedTemporaryFile('w', suffix='.rs',
                                         delete=False) as fh:
            fh.write(code)
            path = fh.name
        try:
            r = subprocess.run(
                ['rustc', '--edition=2021', '--crate-type=lib', path, '-o',
                 path + '.rlib'], capture_output=True, text=True, timeout=90)
            return ('PASS', '') if r.returncode == 0 else (
                'FAIL', r.stderr[:200])
        except subprocess.TimeoutExpired:
            return 'UNKNOWN', 'rustc timeout (heavy toolchain)'
        finally:
            for p in (path, path + '.rlib'):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    return 'UNKNOWN', 'no lightweight checker'


def run_all():
    from epsilon.backends import get_backend, supported_languages
    from epsilon import hir as H
    add, loop = _sample()
    results = []
    for lang in supported_languages():
        be = get_backend(lang)
        tier = 'full' if lang in ('python', 'javascript', 'typescript') else ''
        if not tier:
            from epsilon import lang_tables
            tier = lang_tables.BY_NAME[lang].tier
        src = H.SourceFile(path='m' + be.file_extension(), language=lang,
                           doc='', imports=[], declarations=[add, loop],
                           main_block=[])
        try:
            code = be.render_file(src)
            render_ok, render_err = True, ''
        except Exception as e:
            results.append({'lang': lang, 'tier': tier, 'render_ok': False,
                            'verify': 'UNKNOWN', 'lines': 0,
                            'note': '%s: %s' % (type(e).__name__, e)})
            continue
        state, detail = _verify(lang, code, be.file_extension())
        results.append({'lang': lang, 'tier': tier, 'render_ok': True,
                        'verify': state, 'lines': len(code.splitlines()),
                        'note': detail})
    ok = sum(1 for r in results if r['render_ok'])
    return {'rendered': ok, 'total': len(results), 'results': results}


if __name__ == '__main__':
    import json
    res = run_all()
    print(json.dumps(res, indent=2))
