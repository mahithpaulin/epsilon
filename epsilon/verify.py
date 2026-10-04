"""Epsilon v1 verifiers — syntax gate + smoke exec. Stdlib only."""
import ast
import shutil
import subprocess
import sys
import tempfile
import os


def verify_python(src):
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {'lang': 'python', 'syntax_ok': False, 'exec_ok': False,
                'errors': [{'line': e.lineno, 'col': e.offset, 'msg': str(e.msg)}],
                'stdout': '', 'stderr': str(e), 'returncode': None, 'timed_out': False}
    undef = _undefined_names(tree)
    if undef:
        return {'lang': 'python', 'syntax_ok': True, 'exec_ok': 'unknown',
                'errors': [{'line': None, 'col': None, 'msg': 'undefined names: %s' % sorted(undef), 'rule': 'undef'}],
                'stdout': '', 'stderr': '', 'returncode': None, 'timed_out': False}
    return {'lang': 'python', 'syntax_ok': True, 'exec_ok': 'unknown', 'errors': [],
            'stdout': '', 'stderr': '', 'returncode': None, 'timed_out': False}


def _undefined_names(tree):
    # collect assigned/defined vs loaded; builtins allowed
    import builtins
    defined = set(dir(builtins)) | {'print', 'len', 'range', 'str', 'True', 'False', 'None'}
    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assigned.add(node.name)
            for a in node.args.args:
                assigned.add(a.arg)
        elif isinstance(node, ast.For):
            t = node.target
            if isinstance(t, ast.Name):
                assigned.add(t.id)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            assigned.add(node.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                assigned.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                assigned.add(a.asname or a.name)
    loaded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loaded.add(node.id)
    return {n for n in loaded if n not in assigned and n not in defined}


def verify_js(src):
    node = shutil.which('node')
    if node:
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
            f.write(src)
            path = f.name
        try:
            r = subprocess.run([node, '--check', path], capture_output=True, text=True, timeout=15)
            ok = (r.returncode == 0)
            return {'lang': 'javascript', 'syntax_ok': ok, 'exec_ok': 'unknown',
                    'errors': [] if ok else [{'line': None, 'col': None, 'msg': (r.stderr or r.stdout)[:500]}],
                    'stdout': '', 'stderr': r.stderr, 'returncode': r.returncode, 'timed_out': False}
        except subprocess.TimeoutExpired:
            return {'lang': 'javascript', 'syntax_ok': False, 'exec_ok': False,
                    'errors': [{'line': None, 'col': None, 'msg': 'node --check timeout'}],
                    'stdout': '', 'stderr': '', 'returncode': None, 'timed_out': True}
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
    return verify_js_fallback(src)


def verify_js_fallback(src):
    # strip strings/comments, check balance — heuristic only, never claims guarantee
    s = _strip_js(src)
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    for ch in s:
        if ch in '([{':
            stack.append(ch)
        elif ch in ')]}':
            if not stack or stack[-1] != pairs[ch]:
                return {'lang': 'javascript', 'syntax_ok': False, 'exec_ok': 'unverified-exec',
                        'errors': [{'line': None, 'col': None, 'msg': 'unbalanced %s' % ch}],
                        'stdout': '', 'stderr': '', 'returncode': None, 'timed_out': False}
            stack.pop()
    if stack:
        return {'lang': 'javascript', 'syntax_ok': False, 'exec_ok': 'unverified-exec',
                'errors': [{'line': None, 'col': None, 'msg': 'unclosed %s' % stack}],
                'stdout': '', 'stderr': '', 'returncode': None, 'timed_out': False}
    if 'function' not in s:
        return {'lang': 'javascript', 'syntax_ok': False, 'exec_ok': 'unverified-exec',
                'errors': [{'line': None, 'col': None, 'msg': 'no function found (heuristic)'}],
                'stdout': '', 'stderr': '', 'returncode': None, 'timed_out': False}
    return {'lang': 'javascript', 'syntax_ok': True, 'exec_ok': 'unverified-exec',
            'errors': [], 'stdout': '', 'stderr': 'heuristic only: node absent',
            'returncode': None, 'timed_out': False}


def _strip_js(src):
    out = []
    i, n = 0, len(src)
    st = None
    while i < n:
        if st:
            if src[i] == '\\':
                i += 2
                continue
            if src[i] == st:
                st = None
            i += 1
            continue
        if src[i:i + 2] == '//':
            while i < n and src[i] != '\n':
                i += 1
            continue
        if src[i:i + 2] == '/*':
            i += 2
            while i < n and src[i:i + 2] != '*/':
                i += 1
            i += 2
            continue
        if src[i] in '"\'`':
            st = src[i]
            i += 1
            continue
        out.append(src[i])
        i += 1
    return ''.join(out)


def smoke_python(src, timeout=5.0):
    try:
        r = subprocess.run([sys.executable, '-c', src], capture_output=True, text=True, timeout=timeout)
        return {'exec_ok': r.returncode == 0, 'stdout': r.stdout, 'stderr': r.stderr,
                'returncode': r.returncode, 'timed_out': False}
    except subprocess.TimeoutExpired as e:
        return {'exec_ok': False, 'stdout': '', 'stderr': 'timeout', 'returncode': None, 'timed_out': True}


def smoke_js(src, timeout=5.0):
    node = shutil.which('node')
    if not node:
        return {'exec_ok': 'unverified-exec', 'stdout': '', 'stderr': 'node not found',
                'returncode': None, 'timed_out': False}
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run([node, path], capture_output=True, text=True, timeout=timeout)
        return {'exec_ok': r.returncode == 0, 'stdout': r.stdout, 'stderr': r.stderr,
                'returncode': r.returncode, 'timed_out': False}
    except subprocess.TimeoutExpired:
        return {'exec_ok': False, 'stdout': '', 'stderr': 'timeout', 'returncode': None, 'timed_out': True}
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


def repair_loop(src, lang, max_retries=3):
    """Deterministic repair passes; raises on failure (fail loudly)."""
    errors = []
    for _ in range(max_retries):
        v = verify_python(src) if lang == 'python' else verify_js(src)
        if v['syntax_ok']:
            return src, v
        errors.extend(v['errors'])
        fixed = _auto_fix(src, v['errors'])
        if fixed == src:
            break
        src = fixed
    v = verify_python(src) if lang == 'python' else verify_js(src)
    if not v['syntax_ok']:
        raise RuntimeError('EpsilonRepairFailed %s: %s' % (lang, v['errors'][:2]))
    return src, v


def _auto_fix(src, errors):
    if not errors:
        return src
    msg = str(errors[0].get('msg', ''))
    lines = src.split('\n')
    ln = (errors[0].get('line') or 1) - 1
    if "expected ':'" in msg and 0 <= ln < len(lines):
        line = lines[ln].rstrip()
        if not line.endswith(':'):
            lines[ln] = line + ':'
            return '\n'.join(lines)
    if 'never closed' in msg or 'was never closed' in msg or 'missing' in msg.lower():
        # balance closers at EOF
        s = _strip_py_strings(src)
        stack = []
        for ch in s:
            if ch in '([{':
                stack.append(ch)
            elif ch in ')]}':
                if stack:
                    stack.pop()
        closer = { '(': ')', '[': ']', '{': '}' }
        if stack:
            src = src + ''.join(closer[c] for c in reversed(stack))
            return src
    return src


def _strip_py_strings(src):
    out = []
    i, n = 0, len(src)
    st = None
    triple = False
    while i < n:
        if st:
            if triple and src[i:i + 3] == st * 3:
                st = None
                triple = False
                i += 3
                continue
            if not triple and src[i] == '\\':
                i += 2
                continue
            if not triple and src[i] == st:
                st = None
                i += 1
                continue
            i += 1
            continue
        if src[i:i + 3] in ("'''", '"""'):
            st = src[i]
            triple = True
            i += 3
            continue
        if src[i] in '"\'':
            st = src[i]
            i += 1
            continue
        if src[i] == '#':
            while i < n and src[i] != '\n':
                i += 1
            continue
        out.append(src[i])
        i += 1
    return ''.join(out)
