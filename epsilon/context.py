"""Epsilon v2 project context. Shared memory for plan/generate/validate/repair.

A ProjectContext is a small JSON-serializable snapshot of what the pipeline
knows about one project: its files, symbols, imports, dependencies, test
specs, decisions taken, errors seen, and repairs attempted. Stages read it
for grounding and append to it via the record_* helpers.
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field


@dataclass
class ProjectContext:
    """Mutable pipeline memory for a single project run."""
    requirements: str = ''
    decisions: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    symbols: dict[str, list[str]] = field(default_factory=dict)
    imports: dict[str, list[str]] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    apis: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    known_errors: list = field(default_factory=list)
    repairs: list = field(default_factory=list)
    events: list = field(default_factory=list)

    def record_decision(self, text: str, log=None):
        """Append a decision; optionally emit to an EventLog. Returns event/None."""
        self.decisions.append(text)
        if log is None:
            return None
        return log.emit('plan', 'decided', text, {})

    def record_error(self, error, log=None):
        """Append an error; optionally emit to an EventLog. Returns event/None."""
        self.known_errors.append(error)
        if log is None:
            return None
        return log.emit('validate', 'failed', str(error), {})

    def record_repair(self, attempt, log=None):
        """Append a repair attempt; optionally emit. Returns event/None."""
        self.repairs.append(attempt)
        if log is None:
            return None
        return log.emit('repair', 'repaired', str(attempt), {})

    def to_dict(self) -> dict:
        return {
            'requirements': self.requirements,
            'decisions': list(self.decisions),
            'files': list(self.files),
            'symbols': {k: list(v) for k, v in self.symbols.items()},
            'imports': {k: list(v) for k, v in self.imports.items()},
            'dependencies': list(self.dependencies),
            'apis': list(self.apis),
            'tests': list(self.tests),
            'known_errors': [_jsonable(e) for e in self.known_errors],
            'repairs': [_jsonable(r) for r in self.repairs],
            'events': [_jsonable(e) for e in self.events],
        }


def _jsonable(v):
    if hasattr(v, 'to_dict'):
        try:
            return v.to_dict()
        except Exception:
            pass
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, list):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


def _get(obj, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


_JS_IMPORT_RES = (
    re.compile(r"""import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]"""),
    re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""export\s+[^'"]*?\s+from\s+['"]([^'"]+)['"]"""),
    re.compile(r"""import\(\s*['"]([^'"]+)['"]\s*\)"""),
)

_JS_SYMBOL_RES = (
    re.compile(r'^\s*function\s+([A-Za-z_$][\w$]*)'),
    re.compile(r'^\s*class\s+([A-Za-z_$][\w$]*)'),
    re.compile(r'^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)'),
)


def _py_top_level(tree: ast.AST) -> tuple[list[str], list[str]]:
    """Top-level defined names and imported modules of a parsed .py file."""
    names: list[str] = []
    mods: list[str] = []
    for node in getattr(tree, 'body', []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.append(t.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.append(node.target.id)
        elif isinstance(node, ast.Import):
            for a in node.names:
                mods.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.append(node.module)
                for a in node.names:
                    if a.name != '*':
                        mods.append(node.module + '.' + a.name)
    return names, mods


def _hir_imports(imp) -> list[str]:
    mod = _get(imp, 'module', '') or ''
    out = [mod] if mod else []
    for n in _get(imp, 'names', []) or []:
        if n and n != '*':
            out.append(mod + '.' + n if mod else n)
    return out


def _hir_symbols(decl) -> list[str]:
    name = _get(decl, 'name', '')
    return [name] if name else []


def _js_scan(text: str) -> tuple[list[str], list[str]]:
    """Heuristic top-level names + import specifiers from JS/TS source."""
    names: list[str] = []
    mods: list[str] = []
    for line in text.splitlines():
        for rx in _JS_SYMBOL_RES:
            m = rx.match(line)
            if m:
                names.append(m.group(1))
                break
        for rx in _JS_IMPORT_RES:
            m = rx.search(line)
            if m:
                mods.append(m.group(1))
    return names, mods


def build_context(project, out_dir: str) -> ProjectContext:
    """Build a ProjectContext from HIR plus files already rendered to out_dir.

    Symbols/imports come from two sources, merged deterministically
    (sorted, deduplicated): the HIR declarations/imports, and the generated
    sources under out_dir (.py files via AST, .js/.ts via import-scan plus a
    heuristic top-level name scan). Missing or unparseable files are skipped.
    """
    ctx = ProjectContext()
    ctx.requirements = _get(project, 'description', '') or ''
    meta = _get(project, 'meta', {}) or {}
    if isinstance(meta, dict) and meta.get('requirements'):
        extra = str(meta.get('requirements'))
        ctx.requirements = (ctx.requirements + '\n' + extra).strip()

    files = _get(project, 'files', []) or []
    for f in files:
        path = _get(f, 'path', '') or ''
        if not path:
            continue
        ctx.files.append(path)
        lang = (_get(f, 'language', 'python') or 'python').lower()
        syms: set[str] = set()
        mods: set[str] = set()

        for decl in _get(f, 'declarations', []) or []:
            syms.update(_hir_symbols(decl))
        for imp in _get(f, 'imports', []) or []:
            mods.update(_hir_imports(imp))

        disk_path = os.path.join(out_dir or '.', path)
        try:
            with open(disk_path, 'r', encoding='utf-8') as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            text = ''
        if text and (path.endswith('.py') or lang == 'python'):
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            if tree is not None:
                names, file_mods = _py_top_level(tree)
                syms.update(names)
                mods.update(file_mods)
        elif text and (lang in ('javascript', 'typescript', 'js', 'ts')
                       or path.endswith(('.js', '.ts', '.jsx', '.tsx', '.mjs'))):
            names, file_mods = _js_scan(text)
            syms.update(names)
            mods.update(file_mods)

        ctx.symbols[path] = sorted(syms)
        ctx.imports[path] = sorted(mods)

    for dep in _get(project, 'dependencies', []) or []:
        if isinstance(dep, str):
            ctx.dependencies.append(dep)
            continue
        name = _get(dep, 'name', '') or ''
        spec = _get(dep, 'version_spec', '') or ''
        if name:
            ctx.dependencies.append(name + spec)
    ctx.dependencies.sort()

    for ep in _get(project, 'endpoints', []) or []:
        method = (_get(ep, 'method', 'GET') or 'GET').upper()
        path = _get(ep, 'path', '/') or '/'
        handler = _get(ep, 'handler', '') or ''
        ctx.apis.append('%s %s -> %s' % (method, path, handler))
    ctx.apis.sort()

    for spec in _get(project, 'tests', []) or []:
        label = _get(spec, 'name', '') or _get(spec, 'target', '') or ''
        if label:
            ctx.tests.append(label)
    ctx.tests.sort()

    return ctx
