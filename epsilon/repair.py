"""Epsilon v2 bounded repair engine. Small, ordered, auditable fixes.

CALLER CONTRACT (read before looping on this function):
  * repair() applies at most ONE edit per file per call and never
    re-validates. The caller must re-run validation after each call and may
    loop up to config.repair_iterations times.
  * At most config.max_repair_files files are processed per call, in sorted
    path order. Surplus files are left for later rounds.
  * Every fired strategy is recorded as a RepairAttempt, including failures
    and no-ops. attempts with result 'needs-regeneration' performed NO file
    edit: the orchestrator must re-render exactly that one file, seeded with
    the human-readable hint in RepairAttempt.detail (e.g. via the planner or
    backend with the hint appended), then re-validate. HIR FuncDef carries
    no notes field, so the hint travels in detail, not in the HIR.
  * result is one of 'applied', 'no-op', 'failed', 'needs-regeneration'.

Strategy order is fixed: missing-import, typo-name, manifest-sync (manifest
files only), syntax-patch, regenerate-with-hint (fallback, never edits).

Each strategy exposes predicate(errors, content, project=None, out_dir=None)
-> bool and apply(content, errors, project=None, out_dir=None) -> patched
text | None. The first two arguments are the core contract (report errors for
that file plus current file text); project/out_dir are optional resolution
context used for relative-import lookup and are always supplied by repair().
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import re
import sys
from dataclasses import dataclass

APPLIED = 'applied'
NO_OP = 'no-op'
FAILED = 'failed'
NEEDS_REGEN = 'needs-regeneration'
RESULTS = (APPLIED, NO_OP, FAILED, NEEDS_REGEN)


@dataclass
class RepairAttempt:
    iteration: int = 0
    file: str = ''
    strategy: str = ''
    result: str = NO_OP
    detail: str = ''

    def to_dict(self) -> dict:
        return {'iteration': self.iteration, 'file': self.file,
                'strategy': self.strategy, 'result': self.result,
                'detail': self.detail}

    def __str__(self) -> str:
        return '[%s] %s %s: %s' % (self.strategy, self.result,
                                   self.file, self.detail)


@dataclass
class Strategy:
    name: str
    predicate: object = None
    apply: object = None

    def fires(self, errors, content, project=None, out_dir=None) -> bool:
        return bool(self.predicate(errors, content, project, out_dir))

    def patch(self, content, errors, project=None, out_dir=None):
        return self.apply(content, errors, project, out_dir)


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _ecat(e) -> str:
    return str(_get(e, 'category', '') or '')


def _emsg(e) -> str:
    return str(_get(e, 'message', '') or _get(e, 'msg', '') or '')


def _esym(e) -> str:
    return str(_get(e, 'symbol', '') or '')


def _eline(e):
    v = _get(e, 'line', None)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _esrc(e) -> str:
    return str(_get(e, 'source_file', '') or '')


def _report_errors(report) -> list:
    out = []
    for v in _get(report, 'verdicts', []) or []:
        for e in _get(v, 'errors', []) or []:
            out.append(e)
    out.sort(key=lambda e: (_esrc(e), _eline(e) if _eline(e) is not None else -1,
                            _ecat(e), _emsg(e)))
    return out


def _safe_rel(path: str):
    norm = os.path.normpath(path or '')
    if not norm or norm.startswith('..') or os.path.isabs(norm):
        return None
    return norm


def _record(ctx, events, attempt: RepairAttempt) -> None:
    try:
        _get(ctx, 'repairs', None).append(attempt)
    except AttributeError:
        pass
    if events is None:
        return
    kind = {'applied': 'repaired', 'needs-regeneration': 'decided'}.get(
        attempt.result, 'failed')
    try:
        events.emit('repair', kind, str(attempt),
                    {'file': attempt.file, 'strategy': attempt.strategy,
                     'result': attempt.result})
    except Exception:
        pass


# ---------------------------------------------------------------- imports ---

_NO_MODULE_RE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
_CANNOT_IMPORT_RE = re.compile(
    r"cannot import name ['\"]([^'\"]+)['\"] from ['\"]([^'\"]+)['\"]")
_QUOTED_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")
_DOTTED_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*$')


def _stdlib_root(name: str) -> bool:
    root = (name or '').split('.')[0]
    names = getattr(sys, 'stdlib_module_names', None) or set()
    return bool(root) and root in names


def _dotted_module(path: str):
    p = (path or '').replace(os.sep, '/').lstrip('./')
    if not p.endswith('.py'):
        return None
    mod = p[:-3].replace('/', '.')
    if mod.endswith('.__init__'):
        mod = mod[:len(mod) - len('.__init__')]
    if not mod or not _DOTTED_RE.match(mod):
        return None
    return mod


def _project_symbol_map(project) -> dict:
    """Top-level symbol -> dotted module for .py sources in the HIR project."""
    out: dict[str, str] = {}
    for f in _get(project, 'files', []) or []:
        mod = _dotted_module(_get(f, 'path', '') or '')
        if not mod:
            continue
        for decl in _get(f, 'declarations', []) or []:
            name = _get(decl, 'name', '') or ''
            if name and name not in out:
                out[name] = mod
    return out


def _project_modules(project) -> set[str]:
    out: set[str] = set()
    for f in _get(project, 'files', []) or []:
        mod = _dotted_module(_get(f, 'path', '') or '')
        if mod:
            out.add(mod)
    return out


def _module_mentions(errors) -> list[str]:
    """Ordered candidate module names from imports/symbols errors."""
    found: list[str] = []
    for e in errors:
        if _ecat(e) not in ('imports', 'symbols'):
            continue
        msg = _emsg(e)
        m = _NO_MODULE_RE.search(msg)
        if m:
            found.append(m.group(1))
        m = _CANNOT_IMPORT_RE.search(msg)
        if m:
            found.append(m.group(2))
        sym = _esym(e)
        if sym and _DOTTED_RE.match(sym):
            found.append(sym)
        for a, b in _QUOTED_RE.findall(msg):
            tok = a or b
            if _DOTTED_RE.match(tok) and len(tok) <= 120:
                found.append(tok)
    out: list[str] = []
    for tok in found:
        if tok not in out:
            out.append(tok)
    return out


def _import_line_for(dotted: str, project) -> str | None:
    """Correct import line for a module, or None when it must not be invented.

    stdlib roots get a plain import; names resolving to project files get a
    relative-style from-import or plain import. Anything else (third-party or
    unknown) yields None: third-party lines are never invented here.
    """
    if not dotted or not _DOTTED_RE.match(dotted):
        return None
    root = dotted.split('.')[0]
    if _stdlib_root(root):
        return 'import %s' % root
    modules = _project_modules(project)
    if dotted in modules:
        parent, _, leaf = dotted.rpartition('.')
        if parent:
            return 'from %s import %s' % (parent, leaf)
        return 'import %s' % dotted
    for mod in sorted(modules):
        if mod.split('.')[-1] == root:
            parent, _, leaf = mod.rpartition('.')
            if parent:
                return 'from %s import %s' % (parent, leaf)
            return 'import %s' % mod
    symmap = _project_symbol_map(project)
    if root in symmap:
        return 'from %s import %s' % (symmap[root], root)
    return None


def _has_import_line(content: str, line: str) -> bool:
    want = line.strip()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        tree = None
    if tree is not None:
        if want.startswith('import '):
            mod = want[len('import '):].strip()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == mod:
                            return True
        elif want.startswith('from '):
            m = re.match(r'from\s+(\S+)\s+import\s+(.*)', want)
            if m:
                mod, names = m.group(1), m.group(2)
                want_names = {n.strip() for n in names.split(',')}
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module == mod:
                        have = {a.name for a in node.names}
                        if want_names <= have:
                            return True
        return False
    return want in content


def _insert_after_imports(content: str, line: str) -> str:
    lines = content.split('\n')
    idx = 0
    try:
        tree = ast.parse(content)
    except SyntaxError:
        tree = None
    if tree is not None:
        last = 0
        for node in getattr(tree, 'body', []):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                last = max(last, int(getattr(node, 'end_lineno', 0) or 0))
        if last:
            idx = last
        elif (tree.body and isinstance(tree.body[0], ast.Expr)
              and isinstance(getattr(tree.body[0], 'value', None), ast.Constant)
              and isinstance(tree.body[0].value.value, str)):
            idx = int(tree.body[0].end_lineno or 0)
        lines.insert(idx, line)
        return '\n'.join(lines)
    n = len(lines)
    while idx < n and (not lines[idx].strip()
                       or lines[idx].lstrip().startswith('#')):
        if idx == 0 and lines[idx].startswith('#!'):
            idx += 1
            continue
        idx += 1
    while idx < n and re.match(r'^\s*(import|from)\s+', lines[idx]):
        idx += 1
    lines.insert(idx, line)
    return '\n'.join(lines)


def pred_missing_import(errors, content, project=None, out_dir=None) -> bool:
    for dotted in _module_mentions(errors):
        line = _import_line_for(dotted, project)
        if line is not None and not _has_import_line(content, line):
            return True
    return False


def apply_missing_import(content, errors, project=None, out_dir=None):
    for dotted in _module_mentions(errors):
        line = _import_line_for(dotted, project)
        if line is not None and not _has_import_line(content, line):
            return _insert_after_imports(content, line)
    return None


# ------------------------------------------------------------------ typo ---

_PY_KEYWORDS = frozenset(
    'False None True and as assert async await break class continue def del '
    'elif else except finally for from global if import in is lambda nonlocal '
    'not or pass raise return try while with yield'.split())


def _file_symbols(content: str) -> list[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        tree = None
    if tree is None:
        toks = set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', content or ''))
        return sorted(toks - _PY_KEYWORDS)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != '*':
                    names.add(a.asname or a.name)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
    return sorted(names)


def _symbols_via_validate(out_dir):
    """Optional extra candidates from epsilon.validate; None when absent."""
    try:
        import importlib
        try:
            mod = importlib.import_module('.validate', __package__ or '')
        except Exception:
            mod = importlib.import_module('epsilon.validate')
        fn = getattr(mod, 'resolve_project_symbols', None)
        if fn is None:
            return None
        data = fn(out_dir)
    except Exception:
        return None
    try:
        names: set[str] = set()
        items = data.items() if isinstance(data, dict) else []
        for _path, syms in items:
            if isinstance(syms, dict):
                names.update(str(k) for k in syms.keys())
            elif isinstance(syms, (list, tuple, set)):
                names.update(str(s) for s in syms)
        return sorted(names)
    except Exception:
        return None


def _typo_target(errors, content, project=None, out_dir=None):
    for e in errors:
        if _ecat(e) not in ('symbols',):
            continue
        sym = _esym(e)
        if not sym:
            m = _QUOTED_RE.search(_emsg(e))
            sym = (m.group(1) or m.group(2)) if m else ''
        if not sym or not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', sym):
            continue
        if _eline(e) is None:
            continue
        cands = set(_file_symbols(content))
        cands.update(_project_symbol_map(project or {}).keys())
        extra = _symbols_via_validate(out_dir) if out_dir else None
        if extra:
            cands.update(extra)
        cands.discard(sym)
        match = difflib.get_close_matches(sym, sorted(cands), n=1, cutoff=0.8)
        if match:
            return sym, match[0], _eline(e)
    return None


def pred_typo_name(errors, content, project=None, out_dir=None) -> bool:
    return _typo_target(errors, content, project, out_dir) is not None


def apply_typo_name(content, errors, project=None, out_dir=None):
    hit = _typo_target(errors, content, project, out_dir)
    if hit is None:
        return None
    sym, best, lineno = hit
    lines = content.split('\n')
    idx = lineno - 1
    if not (0 <= idx < len(lines)):
        return None
    fixed, n = re.subn(r'\b%s\b' % re.escape(sym), best, lines[idx], count=1)
    if n == 0:
        return None
    lines[idx] = fixed
    return '\n'.join(lines)


# --------------------------------------------------------------- manifest ---

def _manifest_kind(project, config) -> str:
    lang = (_get(config, 'target_language', '') or '').lower()
    if not lang:
        lang = (_get(project, 'language', 'python') or 'python').lower()
    if lang in ('javascript', 'typescript', 'js', 'ts'):
        return 'package.json'
    return 'requirements.txt'


def _manifest_rel(project, config) -> str:
    return _manifest_kind(project, config)


def _dep_names(errors) -> list[str]:
    found: list[str] = []
    for e in errors:
        if _ecat(e) not in ('dependencies',):
            continue
        sym = _esym(e)
        if sym and re.match(r'^[A-Za-z0-9_.\-]+$', sym):
            found.append(sym)
            continue
        m = _NO_MODULE_RE.search(_emsg(e))
        if m and not _stdlib_root(m.group(1)):
            found.append(m.group(1).split('.')[0])
        for a, b in _QUOTED_RE.findall(_emsg(e)):
            tok = a or b
            if re.match(r'^[A-Za-z0-9_\-]+$', tok) and not _stdlib_root(tok):
                found.append(tok)
    out: list[str] = []
    for tok in found:
        if tok not in out:
            out.append(tok)
    return out


def _declared_dep(content: str, kind: str, name: str) -> bool:
    if kind == 'package.json':
        try:
            data = json.loads(content or '{}')
        except ValueError:
            return False
        deps = data.get('dependencies', {}) if isinstance(data, dict) else {}
        return isinstance(deps, dict) and name in deps
    for line in (content or '').splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        if re.split(r'[<>=!~\s;\[]', s, 1)[0].lower() == name.lower():
            return True
    return False


def _project_dep_spec(project, name: str) -> str:
    for dep in _get(project, 'dependencies', []) or []:
        if isinstance(dep, str):
            if re.split(r'[<>=!~\s;\[]', dep, 1)[0].lower() == name.lower():
                return dep
            continue
        if (_get(dep, 'name', '') or '').lower() == name.lower():
            spec = _get(dep, 'version_spec', '') or ''
            return name + spec
    return name


def _kind_from_rel(rel: str) -> str:
    if (rel or '').lower().endswith('.json'):
        return 'package.json'
    return 'requirements.txt'


def pred_manifest_sync(errors, content, project=None, out_dir=None,
                       kind=None) -> bool:
    if kind is None:
        text = (content or '').strip()
        kind = 'package.json' if text.startswith('{') else 'requirements.txt'
    for name in _dep_names(errors):
        if not _declared_dep(content, kind, name):
            return True
    return False


def apply_manifest_sync(content, errors, project=None, out_dir=None,
                        kind=None):
    if kind is None:
        text = (content or '').strip()
        kind = 'package.json' if text.startswith('{') else 'requirements.txt'
    names = [n for n in _dep_names(errors)
             if not _declared_dep(content, kind, n)]
    if not names:
        return None
    if kind == 'package.json':
        try:
            data = json.loads(content or '{}')
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        deps = data.get('dependencies')
        if deps is None:
            deps = {}
            data['dependencies'] = deps
        if not isinstance(deps, dict):
            return None
        for name in names:
            spec = _project_dep_spec(project, name)
            ver = spec[len(name):] if spec.startswith(name) else ''
            deps.setdefault(name, ver or '*')
        return json.dumps(data, indent=2, sort_keys=True) + '\n'
    lines = (content or '').splitlines()
    for name in names:
        lines.append(_project_dep_spec(project, name))
    return '\n'.join(lines) + '\n'


# ----------------------------------------------------------------- syntax ---

_COLON_RE = re.compile(
    r'^(def|if|elif|else|for|while|with|class|except|finally|try)\b')
_EOF_HINTS = ('closed', 'unclosed', 'unexpected eof', 'unbalanced', 'missing')


def _strip_py_strings(src: str) -> str:
    out: list[str] = []
    i, n = 0, len(src or '')
    quote = None
    triple = False
    while i < n:
        if quote:
            if triple and src[i:i + 3] == quote * 3:
                quote = None
                triple = False
                i += 3
                continue
            if not triple and src[i] == '\\':
                i += 2
                continue
            if not triple and src[i] == quote:
                quote = None
                i += 1
                continue
            i += 1
            continue
        if src[i:i + 3] in ("'''", '"""'):
            quote = src[i]
            triple = True
            i += 3
            continue
        if src[i] in '"\'':
            quote = src[i]
            i += 1
            continue
        if src[i] == '#':
            while i < n and src[i] != '\n':
                i += 1
            continue
        out.append(src[i])
        i += 1
    return ''.join(out)


def _missing_closers(content: str) -> str:
    stack: list[str] = []
    for ch in _strip_py_strings(content):
        if ch in '([{':
            stack.append(ch)
        elif ch in ')]}':
            if stack:
                stack.pop()
    closer = {'(': ')', '[': ']', '{': '}'}
    return ''.join(closer[c] for c in reversed(stack))


def _colon_fix(content: str, lineno: int):
    lines = content.split('\n')
    idx = lineno - 1
    if not (0 <= idx < len(lines)):
        return None
    body = lines[idx].split('#', 1)[0].rstrip()
    if not body or body.endswith(':'):
        return None
    if not _COLON_RE.match(body.strip()):
        return None
    lines[idx] = lines[idx].rstrip() + ':'
    return '\n'.join(lines)


def _syntax_plan(errors, content):
    for e in errors:
        if _ecat(e) != 'syntax':
            continue
        msg = _emsg(e).lower()
        if "expected ':'" in msg and _eline(e) is not None:
            fixed = _colon_fix(content, _eline(e))
            if fixed is not None and fixed != content:
                return fixed, 'appended missing colon on line %d' % _eline(e)
    for e in errors:
        if _ecat(e) != 'syntax':
            continue
        msg = _emsg(e).lower()
        if any(h in msg for h in _EOF_HINTS):
            closers = _missing_closers(content)
            if closers:
                return content + closers, 'appended %r at EOF' % closers
    return None


def pred_syntax_patch(errors, content, project=None, out_dir=None) -> bool:
    return _syntax_plan(errors, content) is not None


def apply_syntax_patch(content, errors, project=None, out_dir=None):
    plan = _syntax_plan(errors, content)
    return plan[0] if plan else None


# ----------------------------------------------------- regenerate w/ hint ---

def _regen_hint(rel: str, errors) -> str:
    bits = []
    for e in errors[:3]:
        bits.append('[%s] %s' % (_ecat(e) or '?', _emsg(e) or ''))
    hint = 're-render %s with hint: %s' % (rel, '; '.join(bits))
    return hint[:500]


def pred_regenerate(errors, content, project=None, out_dir=None) -> bool:
    return bool(errors)


def apply_regenerate(content, errors, project=None, out_dir=None):
    return None


STRATEGIES: list = [
    Strategy('missing-import', pred_missing_import, apply_missing_import),
    Strategy('typo-name', pred_typo_name, apply_typo_name),
    Strategy('manifest-sync', pred_manifest_sync, apply_manifest_sync),
    Strategy('syntax-patch', pred_syntax_patch, apply_syntax_patch),
    Strategy('regenerate-with-hint', pred_regenerate, apply_regenerate),
]


def _repair_source(rel: str, content: str, file_errors, project, out_dir):
    attempts: list[RepairAttempt] = []
    current = content
    for strat in STRATEGIES:
        if strat.name == 'manifest-sync':
            continue
        if not strat.fires(file_errors, current, project, out_dir):
            continue
        if strat.name == 'regenerate-with-hint':
            hint = _regen_hint(rel, file_errors)
            attempts.append(RepairAttempt(file=rel, strategy=strat.name,
                                          result=NEEDS_REGEN, detail=hint))
            break
        try:
            patched = strat.patch(current, file_errors, project, out_dir)
        except Exception as exc:
            attempts.append(RepairAttempt(
                file=rel, strategy=strat.name, result=FAILED,
                detail='apply raised %s' % type(exc).__name__))
            continue
        if patched is None:
            attempts.append(RepairAttempt(
                file=rel, strategy=strat.name, result=FAILED,
                detail='apply returned no patch'))
            continue
        if patched == current:
            attempts.append(RepairAttempt(
                file=rel, strategy=strat.name, result=NO_OP,
                detail='content already satisfies the fix'))
            continue
        attempts.append(RepairAttempt(
            file=rel, strategy=strat.name, result=APPLIED,
            detail=_attempt_detail(strat.name, file_errors, current, patched)))
        current = patched
        break
    return attempts, (current if current != content else None)


def _attempt_detail(name: str, errors, before: str, after: str) -> str:
    if name == 'missing-import':
        for dotted in _module_mentions(errors):
            line = _import_line_for(dotted, None)
            if line and line in after and line not in before:
                return "inserted %r for %s error" % (line, _ecat(errors[0]))
        return 'inserted missing import'
    if name == 'typo-name':
        return 'renamed identifier on reported line'
    if name == 'manifest-sync':
        return 'added missing third-party declaration'
    if name == 'syntax-patch':
        return 'applied single line-targeted syntax edit'
    return 'repair applied'


def _repair_manifest(rel: str, content: str, file_errors, project):
    attempts: list[RepairAttempt] = []
    kind = _kind_from_rel(rel)
    if not pred_manifest_sync(file_errors, content, project, None, kind):
        return attempts, None
    try:
        patched = apply_manifest_sync(content, file_errors, project, None,
                                       kind)
    except Exception as exc:
        attempts.append(RepairAttempt(
            file=rel, strategy='manifest-sync', result=FAILED,
            detail='apply raised %s' % type(exc).__name__))
        return attempts, None
    if patched is None or patched == content:
        attempts.append(RepairAttempt(
            file=rel, strategy='manifest-sync',
            result=NO_OP if patched == content else FAILED,
            detail='manifest already declares the dependency'
            if patched == content else 'apply returned no patch'))
        return attempts, None
    names = _dep_names(file_errors)
    attempts.append(RepairAttempt(
        file=rel, strategy='manifest-sync', result=APPLIED,
        detail='added %s to %s' % (', '.join(names), rel)))
    return attempts, patched


def repair(out_dir, project, report, config, ctx, events=None):
    """Attempt bounded repairs for errors in report. See module docstring.

    Returns (changed, attempts): changed is True when at least one file was
    rewritten; attempts lists every recorded RepairAttempt in firing order.
    """
    file_errors: dict[str, list] = {}
    manifest_rel = _manifest_rel(project, config)
    for e in _report_errors(report):
        cat = _ecat(e)
        src = _esrc(e)
        if cat == 'dependencies':
            key = manifest_rel
        else:
            if not src:
                continue
            key = _safe_rel(src)
            if key is None:
                continue
        file_errors.setdefault(key, []).append(e)

    try:
        limit = int(_get(config, 'max_repair_files', 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(0, limit)
    try:
        base = len(_get(ctx, 'repairs', []) or [])
    except TypeError:
        base = 0

    changed = False
    attempts: list[RepairAttempt] = []
    processed = 0
    for rel in sorted(file_errors):
        if processed >= limit:
            break
        full = os.path.join(out_dir or '.', rel)
        try:
            with open(full, 'r', encoding='utf-8') as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError):
            if rel == manifest_rel:
                content = ''
            else:
                continue
        errs = file_errors[rel]
        if rel == manifest_rel:
            new_attempts, patched = _repair_manifest(rel, content, errs,
                                                     project)
        else:
            new_attempts, patched = _repair_source(rel, content, errs,
                                                   project, out_dir)
        if not new_attempts:
            continue
        processed += 1
        if patched is not None and patched != content:
            try:
                parent = os.path.dirname(full)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(full, 'w', encoding='utf-8') as fh:
                    fh.write(patched)
            except OSError as exc:
                for a in new_attempts:
                    if a.result == APPLIED:
                        a.result = FAILED
                        a.detail = 'write failed: %s' % exc
            else:
                changed = True
        for a in new_attempts:
            a.iteration = base
            _record(ctx, events, a)
            attempts.append(a)
    return changed, attempts
