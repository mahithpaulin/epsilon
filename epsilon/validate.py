"""Epsilon v2 layered validation over a materialized project directory.

Layers (in order): syntax, ast, imports, symbols, structure, types, tests.
``validation_level`` selects the subset that runs:

* minimal  -> syntax + structure
* standard -> syntax + ast + imports + symbols + structure (structure
  includes the deps-manifest consistency check)
* strict   -> all of the above + types (attempt) + tests (discovery)

Stdlib only, Python 3.11 compatible, deterministic (sorted traversal,
no randomness, no wall-clock dependence).
"""
from __future__ import annotations

import ast
import builtins
import functools
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:  # package-relative import (normal use)
    from .errors import (
        PASS, FAIL, UNKNOWN, UNAVAILABLE,
        SYNTAX, AST_VALIDITY, IMPORTS, SYMBOLS, TYPES,
        STRUCTURE, DEPS, TESTS, VALIDATION,
        ERROR, WARNING, INFO,
        EpsilonError, Verdict, err, warn,
    )
except ImportError:  # direct script execution fallback
    from errors import (  # type: ignore[no-redef]
        PASS, FAIL, UNKNOWN, UNAVAILABLE,
        SYNTAX, AST_VALIDITY, IMPORTS, SYMBOLS, TYPES,
        STRUCTURE, DEPS, TESTS, VALIDATION,
        ERROR, WARNING, INFO,
        EpsilonError, Verdict, err, warn,
    )

__all__ = [
    'ScopeBuilder', 'resolve_project_symbols',
    'validate_project', 'validate_file',
    'LAYER_ORDER', 'LEVEL_LAYERS',
]

LAYER_SYNTAX = 'syntax'
LAYER_AST = 'ast'
LAYER_IMPORTS = 'imports'
LAYER_SYMBOLS = 'symbols'
LAYER_STRUCTURE = 'structure'
LAYER_TYPES = 'types'
LAYER_TESTS = 'tests'

LAYER_ORDER = (
    LAYER_SYNTAX, LAYER_AST, LAYER_IMPORTS, LAYER_SYMBOLS,
    LAYER_STRUCTURE, LAYER_TYPES, LAYER_TESTS,
)

LEVEL_LAYERS = {
    'minimal': (LAYER_SYNTAX, LAYER_STRUCTURE),
    'standard': (LAYER_SYNTAX, LAYER_AST, LAYER_IMPORTS, LAYER_SYMBOLS, LAYER_STRUCTURE),
    'strict': (LAYER_SYNTAX, LAYER_AST, LAYER_IMPORTS, LAYER_SYMBOLS,
               LAYER_STRUCTURE, LAYER_TYPES, LAYER_TESTS),
}

_PY_SUFFIXES = ('.py',)
_JS_SUFFIXES = ('.js', '.mjs', '.cjs', '.jsx')
_TS_SUFFIXES = ('.ts', '.tsx', '.mts', '.cts')

_SUBPROCESS_TIMEOUT = 30.0

# Fallback stdlib list used only when sys.stdlib_module_names is missing
# (Python < 3.10). Deliberately broad; exactness does not matter because the
# primary path uses sys.stdlib_module_names.
_CURATED_STDLIB = frozenset({
    '__future__', 'abc', 'argparse', 'array', 'ast', 'asyncio', 'base64',
    'binascii', 'bisect', 'builtins', 'bz2', 'calendar', 'cmath', 'cmd',
    'code', 'codecs', 'collections', 'concurrent', 'configparser', 'contextlib',
    'contextvars', 'copy', 'csv', 'ctypes', 'dataclasses', 'datetime',
    'decimal', 'difflib', 'dis', 'email', 'enum', 'errno', 'faulthandler',
    'fcntl', 'fnmatch', 'fractions', 'functools', 'gc', 'getopt', 'getpass',
    'gettext', 'glob', 'gzip', 'hashlib', 'heapq', 'hmac', 'html', 'http',
    'idlelib', 'importlib', 'inspect', 'io', 'ipaddress', 'itertools', 'json',
    'keyword', 'linecache', 'locale', 'logging', 'lzma', 'mailbox', 'marshal',
    'math', 'mimetypes', 'mmap', 'multiprocessing', 'numbers', 'operator',
    'os', 'pathlib', 'pdb', 'pickle', 'pipes', 'pkgutil', 'platform',
    'plistlib', 'pprint', 'profile', 'pstats', 'pty', 'pwd', 'py_compile',
    'pyclbr', 'queue', 'random', 're', 'readline', 'reprlib', 'resource',
    'secrets', 'select', 'selectors', 'shelve', 'shlex', 'shutil', 'signal',
    'site', 'socket', 'socketserver', 'sqlite3', 'ssl', 'stat', 'statistics',
    'string', 'struct', 'subprocess', 'symtable', 'sys', 'sysconfig',
    'tabnanny', 'tarfile', 'tempfile', 'termios', 'test', 'textwrap',
    'threading', 'time', 'timeit', 'tkinter', 'token', 'tokenize', 'trace',
    'traceback', 'tracemalloc', 'tty', 'turtle', 'types', 'typing',
    'unicodedata', 'unittest', 'urllib', 'uuid', 'venv', 'warnings',
    'wave', 'weakref', 'webbrowser', 'wsgiref', 'xml', 'xmlrpc', 'zipapp',
    'zipfile', 'zipimport', 'zlib', 'zoneinfo',
})

_BUILTIN_NAMES = frozenset(dir(builtins)) | frozenset({
    # Module-level dunders that are valid without explicit binding.
    '__name__', '__file__', '__doc__', '__package__', '__spec__',
    '__loader__', '__cached__', '__builtins__', '__annotations__',
    '__debug__', '__import__', '__all__', '__path__',
})


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _emit(events, stage, kind, message, data=None):
    try:
        if events is not None and hasattr(events, 'emit'):
            events.emit(stage, kind, message, dict(data or {}))
    except Exception:
        pass


def _rel(out_dir, path):
    try:
        return os.path.relpath(os.fspath(path), os.fspath(out_dir))
    except Exception:
        return os.path.basename(os.fspath(path))


def _iter_files(out_dir):
    """Deterministically yield all regular files under out_dir (sorted)."""
    root = Path(out_dir)
    if not root.is_dir():
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        # Prune noise dirs deterministically (still deterministic if kept,
        # but caches would pollute file counts).
        for noisy in ('__pycache__', '.git', '.hg', 'node_modules', '.cache'):
            if noisy in dirnames:
                dirnames.remove(noisy)
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                if p.is_file() and not p.is_symlink():
                    found.append(p)
                elif p.is_symlink():
                    # Count symlinks to files as files (best effort).
                    try:
                        if p.exists():
                            found.append(p)
                    except Exception:
                        pass
            except Exception:
                continue
    found.sort(key=lambda p: p.as_posix())
    return found


def _manifest_sig(out_dir):
    """Cheap staleness key over the handful of manifest paths."""
    root = Path(out_dir)
    sig = []
    for cand in ('requirements.txt', 'requirements-dev.txt', 'pyproject.toml',
                 'package.json'):
        for p in [root / cand] + sorted(root.glob('requirements/*.txt')) \
                if cand.startswith('requirements') else [root / cand]:
            try:
                st = p.stat()
                sig.append((p.as_posix(), st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append((p.as_posix(), None, None))
    return tuple(sig)


def _declared_third_party_cached(out_dir):
    key = (os.path.abspath(out_dir), _manifest_sig(out_dir))
    if os.environ.get('EPSILON_NO_CACHE') != '1' and key in _MANIFEST_CACHE:
        return _MANIFEST_CACHE[key]
    out = _declared_third_party(out_dir)
    if os.environ.get('EPSILON_NO_CACHE') != '1':
        _MANIFEST_CACHE[key] = out
        if len(_MANIFEST_CACHE) > 64:
            _MANIFEST_CACHE.clear()
    return out


def _source_files(out_dir):
    files = _iter_files(out_dir)
    py = [p for p in files if p.suffix.lower() in _PY_SUFFIXES]
    js = [p for p in files if p.suffix.lower() in _JS_SUFFIXES]
    ts = [p for p in files if p.suffix.lower() in _TS_SUFFIXES]
    return py, js, ts


def _read_text(path):
    # Deterministic encoding handling: utf-8, then utf-8-sig, else error.
    # Content-cached (keyed by path+mtime+size); set EPSILON_NO_CACHE=1 to
    # bypass for measurements.
    if os.environ.get('EPSILON_NO_CACHE') != '1':
        key = _fkey(path)
        hit = _TEXT_CACHE.get(key)
        if hit is not None:
            return hit
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    if os.environ.get('EPSILON_NO_CACHE') != '1':
        _TEXT_CACHE[_fkey(path)] = text
        if len(_TEXT_CACHE) > 2000:
            _TEXT_CACHE.clear()
    return text


_TEXT_CACHE: dict = {}
_TREE_CACHE: dict = {}
_TOOL_PATHS: dict = {}
_MANIFEST_CACHE: dict = {}


def _fkey(path):
    try:
        st = os.stat(path)
        return (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (os.path.abspath(path), None, None)


def _cached_tree(path):
    """Parse once per file version. Returns (tree|None, error|None).

    Shares one entry per (path, mtime, size); repair edits change mtime+size
    so entries self-invalidate. Never raises.
    """
    key = _fkey(path)
    if os.environ.get('EPSILON_NO_CACHE') != '1' and key in _TREE_CACHE:
        return _TREE_CACHE[key]
    try:
        text = _read_text(path)
    except Exception as exc:
        return (None, exc)
    try:
        tree = ast.parse(text, filename=os.fspath(path))
    except SyntaxError as exc:
        out = (None, exc)
    else:
        out = (tree, None)
    if os.environ.get('EPSILON_NO_CACHE') != '1':
        _TREE_CACHE[key] = out
        if len(_TREE_CACHE) > 2000:
            _TREE_CACHE.clear()
    return out


def _tool(name):
    """shutil.which, memoized per process (paths don't move mid-run)."""
    if name not in _TOOL_PATHS:
        _TOOL_PATHS[name] = shutil.which(name)
    return _TOOL_PATHS[name]


@functools.lru_cache(maxsize=1)
def _stdlib_names():
    names = getattr(sys, 'stdlib_module_names', None)
    if names:
        try:
            return frozenset(names)
        except Exception:
            pass
    return _CURATED_STDLIB


def _norm_dep(name):
    """PEP-503-ish normalization shared by imports and manifests."""
    return re.sub(r'[-_.]+', '-', (name or '').strip().lower())


def _module_exists(out_dir, dotted):
    parts = [p for p in (dotted or '').split('.') if p]
    if not parts:
        return False
    root = Path(out_dir)
    as_file = root.joinpath(*parts).with_suffix('.py')
    as_pkg = root.joinpath(*parts, '__init__.py')
    try:
        return as_file.is_file() or as_pkg.is_file()
    except Exception:
        return False


def _project_module_map(out_dir):
    """Map dotted module name -> file path for every .py under out_dir."""
    mapping = {}
    root = Path(out_dir)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        if '__pycache__' in dirnames:
            dirnames.remove('__pycache__')
        for name in sorted(filenames):
            if not name.endswith('.py'):
                continue
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(root)
            except Exception:
                continue
            parts = list(rel.with_suffix('').parts)
            if parts and parts[-1] == '__init__':
                parts = parts[:-1]
            if not parts:
                continue
            mapping['.'.join(parts)] = full
    return mapping


# ---------------------------------------------------------------------------
# manifests (requirements / pyproject / package.json)
# ---------------------------------------------------------------------------

def _parse_requirements_text(text):
    deps = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith(('-', '!')):
            # Option lines (-r, -c, -e, ...) are not pinned deps.
            continue
        # Strip environment markers and extras/versions.
        line = line.split(';', 1)[0].strip()
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        m = re.match(r'([A-Za-z0-9_.\-]+)', line)
        if m:
            deps.add(_norm_dep(m.group(1)))
    return deps


def _declared_third_party(out_dir):
    """Return (normalized_names, details_dict)."""
    root = Path(out_dir)
    declared = set()
    sources = []
    # requirements*.txt anywhere top-level or under requirements/
    req_files = sorted(root.glob('requirements*.txt')) + sorted(root.glob('requirements/*.txt'))
    for rf in req_files:
        try:
            deps = _parse_requirements_text(_read_text(rf))
        except Exception:
            continue
        if deps:
            declared |= deps
            sources.append(_rel(out_dir, rf))
    # pyproject.toml (tomllib on 3.11+, else naive regex fallback)
    pyproject = root / 'pyproject.toml'
    if pyproject.is_file():
        try:
            text = _read_text(pyproject)
        except Exception:
            text = ''
        parsed = False
        tomllib = None
        try:
            import tomllib as _tomllib  # Python 3.11+
            tomllib = _tomllib
        except Exception:
            tomllib = None
        if tomllib is not None and text:
            try:
                data = tomllib.loads(text)
                proj = data.get('project', {}) if isinstance(data, dict) else {}
                for dep in proj.get('dependencies', []) or []:
                    m = re.match(r'\s*([A-Za-z0-9_.\-]+)', str(dep))
                    if m and m.group(1).lower() != 'python':
                        declared.add(_norm_dep(m.group(1)))
                opt = proj.get('optional-dependencies', {}) or {}
                for _extra, lst in opt.items():
                    for dep in lst or []:
                        m = re.match(r'\s*([A-Za-z0-9_.\-]+)', str(dep))
                        if m:
                            declared.add(_norm_dep(m.group(1)))
                poetry = data.get('tool', {}).get('poetry', {}).get('dependencies', {}) or {}
                for key in poetry:
                    if str(key).lower() != 'python':
                        declared.add(_norm_dep(str(key)))
                parsed = True
                sources.append('pyproject.toml')
            except Exception:
                parsed = False
        if not parsed and text:
            # Naive fallback: quoted strings inside a dependencies list.
            m = re.search(r'dependencies\s*=\s*\[(.*?)\]', text, re.S)
            if m:
                for q in re.findall(r'"([^"]+)"|\'([^\']+)\'', m.group(1)):
                    dep = q[0] or q[1]
                    mm = re.match(r'\s*([A-Za-z0-9_.\-]+)', dep)
                    if mm:
                        declared.add(_norm_dep(mm.group(1)))
                sources.append('pyproject.toml')
    # package.json (JS/TS deps)
    pkg = root / 'package.json'
    if pkg.is_file():
        try:
            data = json.loads(_read_text(pkg))
        except Exception:
            data = {}
        if isinstance(data, dict):
            for section in ('dependencies', 'devDependencies',
                            'peerDependencies', 'optionalDependencies'):
                block = data.get(section, {}) or {}
                if isinstance(block, dict):
                    for key in block:
                        declared.add(_norm_dep(str(key)))
            sources.append('package.json')
    return declared, {'manifest_sources': sorted(set(sources)), 'declared': sorted(declared)}


def _collect_py_imports(tree):
    """Return list of dicts: module, level, names, lineno, star."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append({'module': a.name, 'level': 0, 'names': [],
                            'lineno': getattr(node, 'lineno', None),
                            'star': False})
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            names = [a.name for a in node.names if a.name != '*']
            star = any(a.name == '*' for a in node.names)
            out.append({'module': mod,
                        'level': int(getattr(node, 'level', 0) or 0),
                        'names': names,
                        'lineno': getattr(node, 'lineno', None),
                        'star': star})
    return out


# ---------------------------------------------------------------------------
# syntax layer
# ---------------------------------------------------------------------------

def _check_js_syntax(path):
    node = _tool('node')
    if node is None:
        return ('unknown', None, 'node not found; cannot verify JavaScript syntax')
    try:
        r = subprocess.run([node, '--check', os.fspath(path)],
                           capture_output=True, text=True,
                           timeout=_SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ('unknown', None, 'node --check timed out')
    except Exception as exc:
        return ('unknown', None, 'node --check failed to run: %s' % exc)
    if r.returncode == 0:
        return ('pass', None, '')
    text = (r.stderr or r.stdout or 'syntax error').strip()
    line = col = None
    m = re.search(r':(\d+)(?::(\d+))?', text)
    if m:
        try:
            line = int(m.group(1))
            col = int(m.group(2)) if m.group(2) else None
        except Exception:
            line = col = None
    return ('fail', (line, col), text[:500])


def _check_ts_syntax(path):
    tsc = _tool('tsc')
    if tsc is None:
        return ('unavailable', None, 'tsc not found; TypeScript check unavailable')
    try:
        r = subprocess.run([tsc, '--noEmit', '--pretty', 'false', os.fspath(path)],
                           capture_output=True, text=True,
                           timeout=_SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return ('unknown', None, 'tsc timed out')
    except Exception as exc:
        return ('unknown', None, 'tsc failed to run: %s' % exc)
    if r.returncode == 0:
        return ('pass', None, '')
    text = (r.stdout or r.stderr or 'type/syntax error').strip()
    line = col = None
    m = re.search(r'\((\d+),(\d+)\)', text) or re.search(r':(\d+):(\d+)', text)
    if m:
        try:
            line, col = int(m.group(1)), int(m.group(2))
        except Exception:
            line = col = None
    return ('fail', (line, col), text[:1000])


def _validate_syntax_layer(out_dir, events=None):
    py_files, js_files, ts_files = _source_files(out_dir)
    errors = []
    unknown = []
    unavailable = []
    checked = 0
    for path in py_files:
        checked += 1
        rel = _rel(out_dir, path)
        try:
            src = _read_text(path)
        except Exception as exc:
            errors.append(err(SYNTAX, 'cannot read file: %s' % exc,
                              source_file=rel, probable_cause='unreadable file',
                              suggested_repair='fix file permissions/encoding'))
            continue
        try:
            compile(src, os.fspath(path), 'exec')
            _cached_tree(path)  # prime the shared parse for later layers
        except SyntaxError as exc:
            errors.append(err(
                SYNTAX, 'syntax error: %s' % (exc.msg or 'invalid syntax'),
                source_file=rel, line=exc.lineno, col=exc.offset,
                probable_cause='invalid Python syntax',
                suggested_repair='fix syntax error'))
        except (ValueError, OverflowError) as exc:
            errors.append(err(SYNTAX, 'syntax error: %s' % exc, source_file=rel,
                              probable_cause='invalid source',
                              suggested_repair='fix syntax error'))
    for path in js_files:
        checked += 1
        rel = _rel(out_dir, path)
        status, _loc, msg = _check_js_syntax(path)
        if status == 'pass':
            continue
        if status == 'fail':
            loc = _loc or (None, None)
            errors.append(err(SYNTAX, 'JavaScript syntax error: %s' % msg,
                              source_file=rel, line=loc[0], col=loc[1],
                              probable_cause='invalid JavaScript syntax',
                              suggested_repair='fix syntax error'))
        else:
            unknown.append(rel)
    for path in ts_files:
        checked += 1
        rel = _rel(out_dir, path)
        status, _loc, msg = _check_ts_syntax(path)
        if status == 'pass':
            continue
        if status == 'fail':
            loc = _loc or (None, None)
            errors.append(err(SYNTAX, 'TypeScript check failed: %s' % msg,
                              source_file=rel, line=loc[0], col=loc[1],
                              probable_cause='invalid TypeScript syntax/types',
                              suggested_repair='fix syntax error'))
        elif status == 'unavailable':
            unavailable.append(rel)
        else:
            unknown.append(rel)
    details = {'files_checked': checked,
               'python_files': len(py_files),
               'js_files': len(js_files),
               'ts_files': len(ts_files)}
    if unknown:
        details['unknown_files'] = sorted(unknown)
        details['reason'] = 'node unavailable or check inconclusive; syntax not verified'
    if unavailable:
        details['unavailable_files'] = sorted(unavailable)
        details.setdefault('reason', 'tsc unavailable; TypeScript check not performed')
    if errors:
        state = FAIL
    elif unknown:
        state = UNKNOWN
    elif unavailable:
        state = UNAVAILABLE
    else:
        state = PASS
    v = Verdict(layer=LAYER_SYNTAX, state=state, errors=errors, details=details)
    _emit(events, 'validate', 'produced' if state == PASS else 'failed',
          'syntax: %s (%d error(s))' % (state, len(errors)),
          {'layer': LAYER_SYNTAX, 'state': state})
    return v


# ---------------------------------------------------------------------------
# ast layer
# ---------------------------------------------------------------------------

def _ast_check_file(path, rel):
    file_errors = []
    tree, perr = _cached_tree(path)
    if perr is not None and tree is None:
        if isinstance(perr, SyntaxError):
            # Syntax layer owns this; still record so the ast layer is honest.
            return [err(AST_VALIDITY, 'unparseable (syntax error): %s' % (perr.msg or ''),
                         source_file=rel, line=perr.lineno, col=perr.offset,
                         probable_cause='invalid Python syntax',
                         suggested_repair='fix syntax error first')]
        return [err(AST_VALIDITY, 'cannot read file: %s' % perr, source_file=rel,
                    probable_cause='unreadable file',
                    suggested_repair='fix file permissions/encoding')]
    # 1. empty function bodies (only pass / docstring / ellipsis).
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body or []
            if not body:
                file_errors.append(err(
                    AST_VALIDITY, "empty function body in '%s'" % node.name,
                    source_file=rel, line=node.lineno, col=node.col_offset + 1,
                    symbol=node.name, probable_cause='function has no statements',
                    suggested_repair='implement the function body'))
                continue
            trivial = True
            for stmt in body:
                if isinstance(stmt, ast.Pass):
                    continue
                if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
                    if stmt.value.value is Ellipsis or isinstance(
                            stmt.value.value, (str, bytes)):
                        continue
                trivial = False
                break
            if trivial:
                file_errors.append(err(
                    AST_VALIDITY, "empty function body in '%s' (only pass/docstring/...)" % node.name,
                    source_file=rel, line=node.lineno, col=node.col_offset + 1,
                    symbol=node.name, probable_cause='placeholder body left in place',
                    suggested_repair='implement the function body'))
    # 2. __future__ placement: must precede all code except an initial docstring.
    seen_code = False
    for i, stmt in enumerate(tree.body):
        is_future = (isinstance(stmt, ast.ImportFrom)
                     and getattr(stmt, 'module', None) == '__future__'
                     and int(getattr(stmt, 'level', 0) or 0) == 0)
        if is_future:
            if seen_code:
                file_errors.append(err(
                    AST_VALIDITY, '__future__ import must occur at the top of the module',
                    source_file=rel, line=stmt.lineno, col=stmt.col_offset + 1,
                    probable_cause='__future__ import after code',
                    suggested_repair='move __future__ import to the top of the file'))
            continue
        if (i == 0 and isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)):
            continue  # module docstring
        seen_code = True
    return file_errors


def _validate_ast_layer(out_dir, events=None):
    py_files, _, _ = _source_files(out_dir)
    errors = []
    funcs = 0
    for path in sorted(p.as_posix() for p in py_files):
        p = Path(path)
        rel = _rel(out_dir, p)
        file_errors = _ast_check_file(p, rel)
        errors.extend(file_errors)
        tree, _perr = _cached_tree(p)
        if tree is not None:
            funcs += sum(1 for n in ast.walk(tree)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    errors.sort(key=lambda e: (e.source_file, e.line or 0, e.symbol))
    state = FAIL if errors else PASS
    v = Verdict(layer=LAYER_AST, state=state, errors=errors,
                details={'files_checked': len(py_files), 'functions_checked': funcs})
    _emit(events, 'validate', 'produced' if state == PASS else 'failed',
          'ast: %s (%d error(s))' % (state, len(errors)),
          {'layer': LAYER_AST, 'state': state})
    return v


# ---------------------------------------------------------------------------
# imports layer
# ---------------------------------------------------------------------------

def _resolve_relative(out_dir, importer, module, level):
    """Resolve a relative import to a dotted name (or None if beyond top)."""
    root = Path(out_dir)
    try:
        rel = Path(importer).relative_to(root)
    except Exception:
        return None
    parts = list(rel.with_suffix('').parts)
    if parts and parts[-1] == '__init__':
        package = parts[:-1]
    else:
        package = parts[:-1]
    if level < 1 or level > len(package) + 1:
        return None
    ancestor = package[:len(package) - level + 1]
    if module:
        return '.'.join(ancestor + module.split('.'))
    return '.'.join(ancestor) if ancestor else ''


def _validate_imports_layer(out_dir, events=None):
    stdlib = _stdlib_names()
    declared, manifest = _declared_third_party_cached(out_dir)
    py_files, _, _ = _source_files(out_dir)
    errors = []
    checked = 0
    third_party_used = set()
    for path in sorted(p.as_posix() for p in py_files):
        p = Path(path)
        rel = _rel(out_dir, p)
        tree, _perr = _cached_tree(p)
        if tree is None:
            continue  # syntax layer reports this
        for item in _collect_py_imports(tree):
            mod, level, lineno = item['module'], item['level'], item['lineno']
            if level and level > 0:
                checked += 1
                dotted = _resolve_relative(out_dir, p, mod, level)
                if mod:
                    ok = dotted is not None and _module_exists(out_dir, dotted)
                else:
                    # `from . import name` — submodule file or package attr.
                    ok = False
                    if dotted is None:
                        ok = False
                    elif dotted == '':
                        ok = any(_module_exists(out_dir, n) for n in item['names'])
                    else:
                        ok = _module_exists(out_dir, dotted) or any(
                            _module_exists(out_dir, dotted + '.' + n)
                            for n in item['names'])
                if not ok:
                    errors.append(err(
                        IMPORTS, "unresolvable relative import '%s'" % (
                            '.' * level + (mod or '')),
                        source_file=rel, line=lineno, symbol=mod or '.',
                        probable_cause='relative module not found in project',
                        suggested_repair='fix module path'))
                continue
            if not mod:
                continue
            top = mod.split('.')[0]
            checked += 1
            if top in stdlib:
                continue
            if _module_exists(out_dir, mod) or _module_exists(out_dir, top):
                continue
            norm = _norm_dep(top)
            if norm in declared:
                third_party_used.add(norm)
                continue
            errors.append(err(
                IMPORTS, "unresolvable import '%s'" % mod,
                source_file=rel, line=lineno, symbol=mod,
                probable_cause='not stdlib, not a project module, not in manifest',
                suggested_repair='add to manifest'))
    errors.sort(key=lambda e: (e.source_file, e.line or 0, e.symbol))
    state = FAIL if errors else PASS
    details = {'imports_checked': checked,
               'declared_third_party': manifest['declared'],
               'third_party_used': sorted(third_party_used)}
    v = Verdict(layer=LAYER_IMPORTS, state=state, errors=errors, details=details)
    _emit(events, 'validate', 'produced' if state == PASS else 'failed',
          'imports: %s (%d error(s))' % (state, len(errors)),
          {'layer': LAYER_IMPORTS, 'state': state})
    return v


# ---------------------------------------------------------------------------
# symbols layer — lexical ScopeBuilder (per-file scopes, never merged)
# ---------------------------------------------------------------------------

class _Scope:
    __slots__ = ('kind', 'bindings')

    def __init__(self, kind):
        self.kind = kind
        self.bindings = {}  # name -> True (presence only; order = insertion)


def _target_names(target):
    """Collect plain names bound by an assignment target."""
    names = []
    if isinstance(target, ast.Name):
        names.append(target.id)
    elif isinstance(target, ast.Starred):
        names.extend(_target_names(target.value))
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            names.extend(_target_names(elt))
    return names


class ScopeBuilder(ast.NodeVisitor):
    """Lexical per-file scope checker.

    Scopes: module (pre-populated so forward function refs resolve),
    class, function (params pre-bound), comprehension. Name loads resolve
    outward through enclosing scopes; within the *same* non-module scope a
    load only sees bindings that precede it (walk order). Attributes and
    exports (``obj.attr``) only require ``obj``.
    """

    def __init__(self, filename=''):
        self.filename = filename
        self.errors = []
        self.scopes = [_Scope('module')]
        self.builtins = set(_BUILTIN_NAMES)

    # -- scope plumbing -------------------------------------------------
    @property
    def current(self):
        return self.scopes[-1]

    def push(self, kind):
        self.scopes.append(_Scope(kind))

    def pop(self):
        self.scopes.pop()

    def bind(self, name, lineno=None):
        if name and isinstance(name, str):
            self.current.bindings[name] = True

    def _resolve(self, name):
        for scope in reversed(self.scopes):
            if name in scope.bindings:
                return True
        return name in self.builtins

    def _check_use(self, node, name):
        if not name or not isinstance(name, str):
            return
        if self._resolve(name):
            return
        self.errors.append(err(
            SYMBOLS, "undefined name '%s'" % name,
            source_file=self.filename,
            line=getattr(node, 'lineno', None),
            col=(getattr(node, 'col_offset', 0) or 0) + 1,
            symbol=name, probable_cause='name never bound in visible scopes',
            suggested_repair='define the name or import it'))

    def _bind_target(self, target):
        """Bind an assignment/for/with target; visit computed bases as uses."""
        if isinstance(target, ast.Name):
            if isinstance(target.ctx, (ast.Store, ast.Param)):
                self.bind(target.id)
            elif isinstance(target.ctx, ast.Del):
                self._check_use(target, target.id)
            else:
                self._check_use(target, target.id)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind_target(elt)
        elif isinstance(target, ast.Subscript):
            self.visit(target.value)
            self.visit(target.slice)
        elif isinstance(target, ast.Attribute):
            self.visit(target.value)
        else:
            self.visit(target)

    def _visit_store_targets(self, targets):
        for t in targets:
            self._bind_target(t)

    # -- module ----------------------------------------------------------
    def visit_Module(self, node):  # noqa: N802 (ast hook)
        for stmt in node.body:
            self.visit(stmt)

    # -- definitions -----------------------------------------------------
    def _visit_funcdef(self, node):
        for dec in node.decorator_list:
            self.visit(dec)
        args = node.args
        for d in list(args.defaults) + [x for x in args.kw_defaults if x is not None]:
            self.visit(d)
        for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)):
            if a.annotation is not None:
                self.visit(a.annotation)
        if args.vararg and args.vararg.annotation is not None:
            self.visit(args.vararg.annotation)
        if args.kwarg and args.kwarg.annotation is not None:
            self.visit(args.kwarg.annotation)
        if getattr(node, 'returns', None) is not None:
            self.visit(node.returns)
        self.bind(node.name)
        self.push('function')
        for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)):
            self.bind(a.arg)
        if args.vararg:
            self.bind(args.vararg.arg)
        if args.kwarg:
            self.bind(args.kwarg.arg)
        for stmt in node.body:
            self.visit(stmt)
        self.pop()

    def visit_FunctionDef(self, node):  # noqa: N802
        self._visit_funcdef(node)

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        self._visit_funcdef(node)

    def visit_ClassDef(self, node):  # noqa: N802
        for dec in node.decorator_list:
            self.visit(dec)
        for base in node.bases:
            self.visit(base)
        for kw in node.keywords:
            self.visit(kw.value)
        self.bind(node.name)
        self.push('class')
        for stmt in node.body:
            self.visit(stmt)
        self.pop()

    def visit_Lambda(self, node):  # noqa: N802
        self.push('function')
        args = node.args
        for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)):
            self.bind(a.arg)
        if args.vararg:
            self.bind(args.vararg.arg)
        if args.kwarg:
            self.bind(args.kwarg.arg)
        for d in list(args.defaults) + [x for x in args.kw_defaults if x is not None]:
            self.visit(d)
        self.visit(node.body)
        self.pop()

    # -- comprehensions (own scope; generators in order) -----------------
    def _visit_comprehension_generators(self, generators):
        for gen in generators:
            self.visit(gen.iter)
            self._bind_target(gen.target)
            for cond in gen.ifs:
                self.visit(cond)

    def visit_ListComp(self, node):  # noqa: N802
        self.push('comprehension')
        self._visit_comprehension_generators(node.generators)
        self.visit(node.elt)
        self.pop()

    def visit_SetComp(self, node):  # noqa: N802
        self.push('comprehension')
        self._visit_comprehension_generators(node.generators)
        self.visit(node.elt)
        self.pop()

    def visit_GeneratorExp(self, node):  # noqa: N802
        self.push('comprehension')
        self._visit_comprehension_generators(node.generators)
        self.visit(node.elt)
        self.pop()

    def visit_DictComp(self, node):  # noqa: N802
        self.push('comprehension')
        self._visit_comprehension_generators(node.generators)
        self.visit(node.key)
        self.visit(node.value)
        self.pop()

    # -- statements with evaluation order different from field order ------
    def visit_Assign(self, node):  # noqa: N802
        self.visit(node.value)
        self._visit_store_targets(node.targets)

    def visit_AnnAssign(self, node):  # noqa: N802
        if node.annotation is not None:
            self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self._bind_target(node.target)

    def visit_AugAssign(self, node):  # noqa: N802
        target = node.target
        if isinstance(target, ast.Name):
            self._check_use(target, target.id)
        else:
            self.visit(target)
        self.visit(node.value)

    def visit_NamedExpr(self, node):  # noqa: N802
        self.visit(node.value)
        self._bind_target(node.target)

    def visit_For(self, node):  # noqa: N802
        self.visit(node.iter)
        self._bind_target(node.target)
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_AsyncFor(self, node):  # noqa: N802
        self.visit_For(node)

    def visit_With(self, node):  # noqa: N802
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars)
        for stmt in node.body:
            self.visit(stmt)

    def visit_AsyncWith(self, node):  # noqa: N802
        self.visit_With(node)

    def visit_ExceptHandler(self, node):  # noqa: N802
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self.bind(node.name)
        for stmt in node.body:
            self.visit(stmt)

    # -- imports / names --------------------------------------------------
    def visit_Import(self, node):  # noqa: N802
        for a in node.names:
            self.bind(a.asname or a.name.split('.')[0])

    def visit_ImportFrom(self, node):  # noqa: N802
        for a in node.names:
            if a.name == '*':
                continue
            self.bind(a.asname or a.name)

    def visit_Name(self, node):  # noqa: N802
        if isinstance(node.ctx, ast.Store):
            self.bind(node.id)
        elif isinstance(node.ctx, ast.Del):
            self._check_use(node, node.id)
        else:
            self._check_use(node, node.id)

    def visit_Global(self, node):  # noqa: N802
        for name in node.names:
            self.bind(name)

    def visit_Nonlocal(self, node):  # noqa: N802
        for name in node.names:
            self.bind(name)


def _collect_module_bindings(tree):
    """Top-level (module-scope) bound names, for forward-reference support.

    Descends through module-level compound statements but never into
    function / class / lambda / comprehension bodies.
    """
    bindings = set()

    def bind_target(t):
        for n in _target_names(t):
            bindings.add(n)

    def walk(stmts):
        for stmt in stmts or []:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bindings.add(stmt.name)
            elif isinstance(stmt, ast.Import):
                for a in stmt.names:
                    bindings.add(a.asname or a.name.split('.')[0])
            elif isinstance(stmt, ast.ImportFrom):
                for a in stmt.names:
                    if a.name != '*':
                        bindings.add(a.asname or a.name)
            elif isinstance(stmt, ast.Assign):
                for t in stmt.targets:
                    bind_target(t)
            elif isinstance(stmt, ast.AnnAssign):
                bind_target(stmt.target)
            elif isinstance(stmt, ast.AugAssign):
                bind_target(stmt.target)
            elif isinstance(stmt, (ast.For, ast.AsyncFor)):
                bind_target(stmt.target)
                walk(stmt.body)
                walk(stmt.orelse)
            elif isinstance(stmt, ast.While):
                walk(stmt.body)
                walk(stmt.orelse)
            elif isinstance(stmt, ast.If):
                walk(stmt.body)
                walk(stmt.orelse)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                for item in stmt.items:
                    if item.optional_vars is not None:
                        bind_target(item.optional_vars)
                walk(stmt.body)
            elif isinstance(stmt, ast.Try):
                walk(stmt.body)
                for h in stmt.handlers:
                    if getattr(h, 'name', None):
                        bindings.add(h.name)
                    walk(getattr(h, 'body', []))
                walk(stmt.orelse)
                walk(stmt.finalbody)
            elif stmt.__class__.__name__ == 'TryStar':
                walk(getattr(stmt, 'body', []))
                for h in getattr(stmt, 'handlers', []):
                    if getattr(h, 'name', None):
                        bindings.add(h.name)
                    walk(getattr(h, 'body', []))
                walk(getattr(stmt, 'orelse', []))
                walk(getattr(stmt, 'finalbody', []))
            elif stmt.__class__.__name__ == 'Match':
                for case in getattr(stmt, 'cases', []):
                    for sub in ast.walk(getattr(case, 'pattern', None) or ast.Constant(value=None)):
                        name = getattr(sub, 'name', None)
                        if isinstance(sub, ast.Name) and name:
                            bindings.add(name)
                        elif sub.__class__.__name__ in ('MatchAs', 'MatchStar') and name:
                            bindings.add(name)
                        elif sub.__class__.__name__ == 'MatchMapping':
                            rest = getattr(sub, 'rest', None)
                            if rest:
                                bindings.add(rest)
                    walk(getattr(case, 'body', []))
            elif isinstance(stmt, ast.Expr):
                for sub in ast.walk(stmt.value):
                    if isinstance(sub, ast.NamedExpr):
                        bind_target(sub.target)
            # Other statements bind nothing at module level.

    walk(list(getattr(tree, 'body', [])))
    return bindings


def _check_file_symbols(path, rel):
    tree, perr = _cached_tree(path)
    if tree is None:
        if perr is not None and not isinstance(perr, SyntaxError):
            return [err(SYMBOLS, 'cannot read file: %s' % perr, source_file=rel,
                        probable_cause='unreadable file',
                        suggested_repair='fix file permissions/encoding')], set()
        return [], set()  # syntax layer owns this; stay silent, never guess
    bindings = set(_collect_module_bindings(tree))  # computed once, reused
    builder = ScopeBuilder(filename=rel)
    builder.scopes[0].bindings = {n: True for n in bindings}
    try:
        builder.visit(tree)
    except Exception as exc:
        builder.errors.append(err(SYMBOLS, 'symbol analysis failed: %s' % exc,
                                  source_file=rel,
                                  probable_cause='internal walker error',
                                  suggested_repair='report validator bug'))
    return builder.errors, bindings


def resolve_project_symbols(out_dir):
    """Lexical per-file symbol resolution plus cross-file from-import checks.

    Each file gets its own ScopeBuilder (scopes are never merged across
    files — the v1 bug). ``from pkg.mod import name`` additionally requires
    ``name`` to exist in that module's top-level defs.
    """
    root = Path(out_dir)
    py_files = sorted(
        (p for p in _iter_files(out_dir) if p.suffix.lower() == '.py'),
        key=lambda p: p.as_posix())
    errors = []
    defs_by_module = {}
    trees = {}
    for path in py_files:
        rel = _rel(out_dir, path)
        tree, _perr = _cached_tree(path)
        if tree is None:
            continue
        trees[path] = tree
        defs_by_module[path] = set(_collect_module_bindings(tree))
    for path in py_files:
        rel = _rel(out_dir, path)
        file_errors, _defs = _check_file_symbols(path, rel)
        errors.extend(file_errors)
        tree = trees.get(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if int(getattr(node, 'level', 0) or 0) != 0:
                continue
            mod = getattr(node, 'module', None)
            if not mod:
                continue
            target = None
            parts = mod.split('.')
            candidate = root.joinpath(*parts).with_suffix('.py')
            candidate_pkg = root.joinpath(*parts, '__init__.py')
            try:
                if candidate.is_file():
                    target = candidate
                elif candidate_pkg.is_file():
                    target = candidate_pkg
            except Exception:
                target = None
            if target is None:
                continue  # stdlib / third-party / missing (imports layer owns it)
            available = defs_by_module.get(target, set())
            for a in node.names:
                if a.name == '*':
                    continue
                if a.name not in available:
                    errors.append(err(
                        SYMBOLS,
                        "cannot import name '%s' from '%s' (not defined there)" % (a.name, mod),
                        source_file=rel, line=getattr(node, 'lineno', None),
                        col=(getattr(node, 'col_offset', 0) or 0) + 1,
                        symbol=a.name, probable_cause='export does not exist in target module',
                        suggested_repair='fix module path'))
    errors.sort(key=lambda e: (e.source_file, e.line or 0, e.col or 0, e.symbol))
    return errors


def _validate_symbols_layer(out_dir, events=None):
    try:
        errors = resolve_project_symbols(out_dir)
    except Exception as exc:
        errors = [err(SYMBOLS, 'symbol analysis failed: %s' % exc,
                      probable_cause='internal validator error',
                      suggested_repair='report validator bug')]
    state = FAIL if errors else PASS
    v = Verdict(layer=LAYER_SYMBOLS, state=state, errors=errors,
                details={'files_with_errors': len({e.source_file for e in errors})})
    _emit(events, 'validate', 'produced' if state == PASS else 'failed',
          'symbols: %s (%d error(s))' % (state, len(errors)),
          {'layer': LAYER_SYMBOLS, 'state': state})
    return v


# ---------------------------------------------------------------------------
# structure layer (entry + tests presence + manifest consistency + file count)
# ---------------------------------------------------------------------------

def _discover_test_files(out_dir):
    root = Path(out_dir)
    found = []
    for path in _iter_files(out_dir):
        try:
            rel = path.relative_to(root).as_posix()
        except Exception:
            continue
        name = path.name
        suffix = path.suffix.lower()
        is_py_test = (suffix == '.py' and (
            name.startswith('test_') or name.endswith('_test.py')
            or '/tests/' in '/' + rel or rel.startswith('tests/')
            or '/test/' in '/' + rel or rel.startswith('test/')))
        is_js_test = (suffix in ('.js', '.jsx', '.mjs', '.cjs',
                                 '.ts', '.tsx', '.mts', '.cts')
                      and ('.test.' in name or '.spec.' in name
                           or '/__tests__/' in '/' + rel
                           or rel.startswith('__tests__/')))
        if is_py_test or is_js_test:
            found.append(path)
    found.sort(key=lambda p: p.as_posix())
    return found


def _count_py_test_cases(path):
    tree, _perr = _cached_tree(path)
    if tree is None:
        return 0
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name.startswith('test'))


def _third_party_imports(out_dir):
    """Normalized third-party-looking top-level imports (absolute, non-stdlib, non-project)."""
    stdlib = _stdlib_names()
    py_files, _, _ = _source_files(out_dir)
    used = {}
    for path in sorted(p.as_posix() for p in py_files):
        tree, _perr = _cached_tree(Path(path))
        if tree is None:
            continue
        for item in _collect_py_imports(tree):
            if item['level']:
                continue
            mod = item['module']
            if not mod:
                continue
            top = mod.split('.')[0]
            if top in stdlib:
                continue
            if _module_exists(out_dir, mod) or _module_exists(out_dir, top):
                continue
            used.setdefault(_norm_dep(top), set()).add(top)
    return used


def _validate_structure_layer(out_dir, project, config, events=None):
    errors = []
    max_files = getattr(config, 'max_files', 40)
    try:
        max_files = int(max_files)
    except Exception:
        max_files = 40
    entry = getattr(project, 'entry', '') or ''
    if entry:
        entry_path = Path(out_dir) / entry
        try:
            exists = entry_path.is_file()
        except Exception:
            exists = False
        if not exists:
            errors.append(err(
                STRUCTURE, "entry file '%s' does not exist" % entry,
                source_file=str(entry), probable_cause='entry path wrong or file not generated',
                suggested_repair='fix entry path'))
    declared, manifest = _declared_third_party_cached(out_dir)
    used = _third_party_imports(out_dir)
    for norm in sorted(used):
        if norm not in declared:
            errors.append(err(
                DEPS, "third-party import '%s' missing from manifest" % sorted(used[norm])[0],
                probable_cause='dependency not declared',
                suggested_repair='add to manifest'))
    for norm in sorted(declared):
        if norm not in used:
            errors.append(warn(
                DEPS, "declared dependency '%s' is never imported" % norm,
                probable_cause='unused or stale manifest entry',
                suggested_repair='remove unused dependency'))
    tests_declared = list(getattr(project, 'tests', None) or [])
    test_files = _discover_test_files(out_dir)
    if tests_declared and not test_files:
        errors.append(err(
            STRUCTURE, 'project declares %d test spec(s) but no test files were found' % len(tests_declared),
            probable_cause='tests not generated',
            suggested_repair='generate test files'))
    try:
        file_count = len([p for p in _iter_files(out_dir)
                          if not p.name.endswith(('.pyc', '.pyo'))])
    except Exception:
        file_count = 0
    if file_count > max_files:
        errors.append(err(
            STRUCTURE, 'file count %d exceeds max_files=%d' % (file_count, max_files),
            probable_cause='too many files generated',
            suggested_repair='reduce generated files'))
    hard = [e for e in errors if e.severity == ERROR]
    state = FAIL if hard else PASS
    details = {'entry': entry, 'file_count': file_count, 'max_files': max_files,
               'declared_deps': manifest['declared'],
               'imported_third_party': sorted(used),
               'test_files': sorted(_rel(out_dir, p) for p in test_files)}
    v = Verdict(layer=LAYER_STRUCTURE, state=state, errors=errors, details=details)
    _emit(events, 'validate', 'produced' if state == PASS else 'failed',
          'structure: %s (%d error(s))' % (state, len(hard)),
          {'layer': LAYER_STRUCTURE, 'state': state})
    return v


# ---------------------------------------------------------------------------
# types layer (mypy if present, else UNAVAILABLE — never claim)
# ---------------------------------------------------------------------------

def _validate_types_layer(out_dir, config=None, events=None):
    mypy = _tool('mypy')
    if mypy is None:
        v = Verdict(layer=LAYER_TYPES, state=UNAVAILABLE, errors=[],
                    details={'reason': 'mypy not installed; type check not performed'})
        _emit(events, 'validate', 'produced', 'types: UNAVAILABLE (mypy missing)',
              {'layer': LAYER_TYPES, 'state': UNAVAILABLE})
        return v
    py_files = sorted((p for p in _iter_files(out_dir) if p.suffix.lower() == '.py'),
                      key=lambda p: p.as_posix())
    if not py_files:
        v = Verdict(layer=LAYER_TYPES, state=PASS, errors=[],
                    details={'files_checked': 0, 'note': 'no Python files'})
        _emit(events, 'validate', 'produced', 'types: PASS (nothing to check)',
              {'layer': LAYER_TYPES, 'state': PASS})
        return v
    timeout = float(getattr(config, 'timeout_secs', 0) or 0) or 60.0
    try:
        r = subprocess.run([mypy] + [os.fspath(p) for p in py_files],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        v = Verdict(layer=LAYER_TYPES, state=UNKNOWN, errors=[],
                    details={'reason': 'mypy timed out; result unknown'})
        _emit(events, 'validate', 'failed', 'types: UNKNOWN (mypy timeout)',
              {'layer': LAYER_TYPES, 'state': UNKNOWN})
        return v
    except Exception as exc:
        v = Verdict(layer=LAYER_TYPES, state=UNKNOWN, errors=[],
                    details={'reason': 'mypy failed to run: %s' % exc})
        _emit(events, 'validate', 'failed', 'types: UNKNOWN (runner error)',
              {'layer': LAYER_TYPES, 'state': UNKNOWN})
        return v
    text = (r.stdout or '').strip()
    if r.returncode == 0:
        v = Verdict(layer=LAYER_TYPES, state=PASS, errors=[],
                    details={'files_checked': len(py_files), 'mypy_output': text[:500]})
        _emit(events, 'validate', 'produced', 'types: PASS',
              {'layer': LAYER_TYPES, 'state': PASS})
        return v
    errors = []
    for line in text.splitlines():
        m = re.match(r'^(.*?):(\d+):(?:(\d+):)?\s*(.*)$', line.strip())
        if m:
            fname, lineno, col, msg = m.group(1), m.group(2), m.group(3), m.group(4)
            try:
                lineno_i = int(lineno)
            except Exception:
                lineno_i = None
            try:
                col_i = int(col) if col else None
            except Exception:
                col_i = None
            errors.append(err(TYPES, msg, source_file=fname,
                              line=lineno_i, col=col_i,
                              probable_cause='mypy reported a type error',
                              suggested_repair='fix type annotation'))
        elif line.strip():
            errors.append(err(TYPES, line.strip()[:500],
                              probable_cause='mypy reported an issue',
                              suggested_repair='fix type annotation'))
    errors.sort(key=lambda e: (e.source_file, e.line or 0, e.col or 0))
    v = Verdict(layer=LAYER_TYPES, state=FAIL, errors=errors,
                details={'files_checked': len(py_files)})
    _emit(events, 'validate', 'failed',
          'types: FAIL (%d error(s))' % len(errors),
          {'layer': LAYER_TYPES, 'state': FAIL})
    return v


# ---------------------------------------------------------------------------
# tests-discovery layer
# ---------------------------------------------------------------------------

def _validate_tests_layer(out_dir, project, events=None):
    test_files = _discover_test_files(out_dir)
    per_file = {}
    total = 0
    for path in test_files:
        rel = _rel(out_dir, path)
        if path.suffix.lower() == '.py':
            n = _count_py_test_cases(path)
        else:
            n = 0  # JS/TS: presence only (no lightweight runner here)
        per_file[rel] = n
        total += n
    tests_declared = list(getattr(project, 'tests', None) or [])
    errors = []
    if tests_declared and not test_files:
        errors.append(err(
            TESTS, 'project declares %d test spec(s) but no test files were discovered'
            % len(tests_declared),
            probable_cause='tests not generated',
            suggested_repair='generate test files'))
        state = FAIL
    else:
        state = PASS
    details = {'test_files': sorted(per_file),
               'per_file_cases': per_file,
               'test_function_count': total,
               'declared_specs': len(tests_declared)}
    v = Verdict(layer=LAYER_TESTS, state=state, errors=errors, details=details)
    _emit(events, 'validate', 'produced' if state == PASS else 'failed',
          'tests: %s (%d file(s), %d case(s))' % (state, len(test_files), total),
          {'layer': LAYER_TESTS, 'state': state})
    return v


# ---------------------------------------------------------------------------
# entry points
# ---------------------------------------------------------------------------

def validate_project(out_dir, project, config, events=None):
    """Run layered validation over a materialized project directory.

    Returns verdicts in LAYER_ORDER restricted by config.validation_level.
    Never raises for ordinary validation findings; an unexpected internal
    failure becomes an UNKNOWN verdict (category 'validation').
    """
    level = str(getattr(config, 'validation_level', 'standard') or 'standard').lower()
    layers = LEVEL_LAYERS.get(level, LEVEL_LAYERS['standard'])
    _emit(events, 'validate', 'started',
          'validation started (level=%s)' % level, {'level': level})
    verdicts = []
    for layer in LAYER_ORDER:
        if layer not in layers:
            continue
        try:
            if layer == LAYER_SYNTAX:
                verdicts.append(_validate_syntax_layer(out_dir, events))
            elif layer == LAYER_AST:
                verdicts.append(_validate_ast_layer(out_dir, events))
            elif layer == LAYER_IMPORTS:
                verdicts.append(_validate_imports_layer(out_dir, events))
            elif layer == LAYER_SYMBOLS:
                verdicts.append(_validate_symbols_layer(out_dir, events))
            elif layer == LAYER_STRUCTURE:
                verdicts.append(_validate_structure_layer(out_dir, project, config, events))
            elif layer == LAYER_TYPES:
                verdicts.append(_validate_types_layer(out_dir, config, events))
            elif layer == LAYER_TESTS:
                verdicts.append(_validate_tests_layer(out_dir, project, events))
        except Exception as exc:
            verdicts.append(Verdict(
                layer=layer, state=UNKNOWN,
                errors=[err(VALIDATION, '%s layer crashed: %s' % (layer, exc),
                            probable_cause='internal validator error',
                            suggested_repair='report validator bug')],
                details={'reason': str(exc)[:300]}))
            _emit(events, 'validate', 'failed',
                  '%s layer crashed: %s' % (layer, exc), {'layer': layer})
    _emit(events, 'validate', 'produced',
          'validation complete: %s' % [(v.layer, v.state) for v in verdicts],
          {'verdicts': [(v.layer, v.state) for v in verdicts]})
    return verdicts


def validate_file(path, language):
    """Single-file fast path: syntax (+ ast/symbols for Python)."""
    lang = (language or '').strip().lower()
    if lang in ('js', 'javascript'):
        lang = 'javascript'
    elif lang in ('ts', 'typescript'):
        lang = 'typescript'
    else:
        lang = 'python'
    p = Path(path)
    verdicts = []
    if not p.is_file():
        verdicts.append(Verdict(
            layer=LAYER_SYNTAX, state=FAIL,
            errors=[err(SYNTAX, 'file does not exist: %s' % path,
                        source_file=str(path),
                        probable_cause='wrong path',
                        suggested_repair='fix file path')],
            details={'path': str(path)}))
        return verdicts
    if lang == 'python':
        try:
            src = _read_text(p)
        except Exception as exc:
            return [Verdict(layer=LAYER_SYNTAX, state=FAIL,
                             errors=[err(SYNTAX, 'cannot read file: %s' % exc,
                                         source_file=str(path),
                                         probable_cause='unreadable file',
                                         suggested_repair='fix file permissions/encoding')],
                             details={'path': str(path)})]
        try:
            compile(src, os.fspath(p), 'exec')
            verdicts.append(Verdict(layer=LAYER_SYNTAX, state=PASS, errors=[],
                                    details={'path': str(path)}))
        except SyntaxError as exc:
            verdicts.append(Verdict(
                layer=LAYER_SYNTAX, state=FAIL,
                errors=[err(SYNTAX, 'syntax error: %s' % (exc.msg or 'invalid syntax'),
                            source_file=str(path), line=exc.lineno, col=exc.offset,
                            probable_cause='invalid Python syntax',
                            suggested_repair='fix syntax error')],
                details={'path': str(path)}))
        ast_errors = _ast_check_file(p, str(path))
        verdicts.append(Verdict(layer=LAYER_AST,
                                state=FAIL if ast_errors else PASS,
                                errors=ast_errors, details={'path': str(path)}))
        sym_errors, _defs = _check_file_symbols(p, str(path))
        verdicts.append(Verdict(layer=LAYER_SYMBOLS,
                                state=FAIL if sym_errors else PASS,
                                errors=sym_errors, details={'path': str(path)}))
        return verdicts
    if lang == 'javascript':
        status, loc, msg = _check_js_syntax(p)
        if status == 'pass':
            return [Verdict(layer=LAYER_SYNTAX, state=PASS, errors=[],
                             details={'path': str(path)})]
        if status == 'fail':
            loc = loc or (None, None)
            return [Verdict(layer=LAYER_SYNTAX, state=FAIL,
                             errors=[err(SYNTAX, 'JavaScript syntax error: %s' % msg,
                                         source_file=str(path), line=loc[0], col=loc[1],
                                         probable_cause='invalid JavaScript syntax',
                                         suggested_repair='fix syntax error')],
                             details={'path': str(path)})]
        return [Verdict(layer=LAYER_SYNTAX, state=UNKNOWN, errors=[],
                         details={'path': str(path), 'reason': msg})]
    status, loc, msg = _check_ts_syntax(p)
    if status == 'pass':
        return [Verdict(layer=LAYER_SYNTAX, state=PASS,
                         errors=[], details={'path': str(path)})]
    if status == 'fail':
        loc = loc or (None, None)
        return [Verdict(layer=LAYER_SYNTAX, state=FAIL,
                         errors=[err(SYNTAX, 'TypeScript check failed: %s' % msg,
                                     source_file=str(path), line=loc[0], col=loc[1],
                                     probable_cause='invalid TypeScript syntax/types',
                                     suggested_repair='fix syntax error')],
                         details={'path': str(path)})]
    state = UNAVAILABLE if status == 'unavailable' else UNKNOWN
    return [Verdict(layer=LAYER_SYNTAX, state=state, errors=[],
                    details={'path': str(path), 'reason': msg})]


def verify_python(src: str) -> dict:
    """v1-shaped single-snippet check backed by v2 verdicts (compat surface)."""
    verdicts = validate_file_content(src, 'python')
    return {'syntax_ok': all(v.state == PASS for v in verdicts),
            'errors': [e.to_dict() if hasattr(e, 'to_dict') else e
                       for v in verdicts for e in v.errors]}


def verify_js(src: str) -> dict:
    """v1-shaped single-snippet check backed by v2 verdicts (compat surface)."""
    verdicts = validate_file_content(src, 'javascript')
    return {'syntax_ok': all(v.state == PASS for v in verdicts),
            'errors': [e.to_dict() if hasattr(e, 'to_dict') else e
                       for v in verdicts for e in v.errors]}


def smoke_python(src: str, timeout: float = 5.0) -> dict:
    """v1-shaped exec probe backed by sandbox.run (compat surface)."""
    import sys as _sys
    from . import sandbox as _SB
    res = _SB.run([_sys.executable, '-c', src], cwd='.', timeout=timeout)
    return {'exec_ok': res.exit_code == 0 and not res.timed_out,
            'stdout': res.stdout, 'stderr': res.stderr,
            'returncode': res.exit_code, 'timed_out': res.timed_out}


def repair_loop(src: str, lang: str = 'python', max_retries: int = 3) -> tuple:
    """Bounded deterministic syntax repair. Raises RuntimeError when stuck.

    Strategies are line-targeted single edits (missing ':' on block openers,
    unbalanced closers at EOF). Never claims success without recompiling.
    """
    lang = (lang or 'python').lower()
    if lang not in ('python', 'py'):
        raise ValueError('repair_loop v1-compat supports python only')
    cur = src
    for _ in range(max(0, max_retries)):
        try:
            compile(cur, '<repair>', 'exec')
            return cur, {'syntax_ok': True, 'errors': []}
        except SyntaxError as exc:
            fixed = _repair_once(cur, exc)
            if fixed == cur:
                break
            cur = fixed
    try:
        compile(cur, '<repair>', 'exec')
        return cur, {'syntax_ok': True, 'errors': []}
    except SyntaxError as exc:
        raise RuntimeError('EpsilonRepairFailed: %s at line %s' % (exc.msg, exc.lineno))


def _repair_once(src: str, exc: SyntaxError) -> str:
    lines = src.split('\n')
    ln = (exc.lineno or 1) - 1
    msg = str(exc.msg or '')
    if 0 <= ln < len(lines):
        stripped = lines[ln].rstrip()
        if re.search(r'^\s*(def |if |elif |else|for |while |try|except|finally|with |class )', lines[ln]) \
                and not stripped.endswith(':'):
            lines[ln] = stripped + ':'
            return '\n'.join(lines)
        if 'expected' in msg and ':' in msg and not stripped.endswith(':'):
            lines[ln] = stripped + ':'
            return '\n'.join(lines)
    if 'never closed' in msg or 'was never closed' in msg or 'EOF' in msg:
        s = _strip_strings(src)
        stack: list[str] = []
        for ch in s:
            if ch in '([{':
                stack.append(ch)
            elif ch in ')]}' and stack:
                stack.pop()
        closer = {'(': ')', '[': ']', '{': '}'}
        if stack:
            return src + ''.join(closer[c] for c in reversed(stack))
    return src


def _strip_strings(src: str) -> str:
    out: list[str] = []
    i, n = 0, len(src)
    st: str | None = None
    while i < n:
        if st:
            if src[i] == '\\':
                i += 2
                continue
            if src[i] == st:
                st = None
            i += 1
            continue
        if src[i] == '#':
            while i < n and src[i] != '\n':
                i += 1
            continue
        if src[i] in '"\'':
            st = src[i]
            i += 1
            continue
        out.append(src[i])
        i += 1
    return ''.join(out)


def validate_file_content(code: str, language: str) -> list:
    """Validate source text by spilling it to a temp file, then validate_file."""
    import tempfile
    lang = (language or 'python').strip().lower()
    suffix = '.py'
    if lang in ('javascript', 'js'):
        suffix = '.js'
    elif lang in ('typescript', 'ts'):
        suffix = '.ts'
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix='epsilon-')
    try:
        with open(fd, 'w') as fh:
            fh.write(code)
        return validate_file(tmp, language)
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass
