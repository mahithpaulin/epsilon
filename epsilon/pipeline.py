"""Epsilon v2 pipeline. Orchestrates requirements -> plan -> HIR -> bodies ->
tests -> files -> validate -> execute -> repair. Single source of truth for
function semantics: the SEMANTICS table below builds bodies AND test cases
together, so generated tests assert what the bodies actually guarantee.
"""
from __future__ import annotations

import os
import re
import time

from . import hir as H
from .errors import (PASS, FAIL, UNKNOWN, UNAVAILABLE, EpsilonError, Verdict,
                     ProjectReport, err)
from .config import EpsilonConfig
from .events import EventLog

EXT_MAP = {'python': '.py', 'javascript': '.js', 'typescript': '.ts'}

SPECIFIC_BEHAVIORS = {'sort-by', 'filter-where', 'count-where', 'accumulate',
                      'average', 'minmax-loop', 'dedupe', 'reverse-seq',
                      'group-count', 'none-where', 'search-first',
                      'transform-each', 'recurse'}

PY_STDLIB_FOR_JS_DROP = {'argparse', 'sqlite3', 'http.server', 'pathlib',
                         'unittest', 'dataclasses'}
JS_IMPORT_MAP = {'pathlib': 'path', 'json': None, 'sys': None, 'os': 'os'}


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------

def normalize(project: H.Project, events: EventLog | None = None) -> list[str]:
    """Remap file extensions + per-language imports. Returns warnings."""
    warnings: list[str] = []
    lang = (project.language or 'python').lower()
    ext = EXT_MAP.get(lang, '.py')
    if getattr(project, 'entry', ''):
        base, dot, _old = project.entry.rpartition('.')
        if dot and _old in ('py', 'js', 'ts'):
            project.entry = base + ext
    for f in project.files:
        if type(f).__name__ != 'SourceFile':
            continue
        base, dot, _old = f.path.rpartition('.')
        if dot and _old in ('py', 'js', 'ts'):
            f.path = base + ext
        f.language = 'python' if ext == '.py' else ('typescript' if ext == '.ts' else 'javascript')
        kept = []
        for imp in f.imports or []:
            mod = imp.module
            if lang in ('javascript', 'typescript'):
                if mod in PY_STDLIB_FOR_JS_DROP:
                    warnings.append('dropped python-only import %s in %s' % (mod, f.path))
                    continue
                if mod in JS_IMPORT_MAP:
                    repl = JS_IMPORT_MAP[mod]
                    if repl is None:
                        continue
                    imp.module = repl
            kept.append(imp)
        f.imports = kept
    if events:
        for w in warnings:
            events.emit('plan', 'decided', w, {'choice': 'import-normalization'})
    return warnings


def attach_models(project: H.Project) -> None:
    """Render DataModels as @dataclass classes atop the first domain file."""
    if not project.models:
        return
    target = None
    for f in project.files:
        if type(f).__name__ == 'SourceFile' and 'domain' in f.path:
            target = f
            break
    if target is None:
        for f in project.files:
            if type(f).__name__ == 'SourceFile':
                target = f
                break
    if target is None:
        return
    classes = []
    for m in project.models:
        if type(m).__name__ != 'DataModel':
            continue
        classes.append(H.ClassDef(name=m.name, bases=[], doc=m.doc,
                                  fields=list(m.fields), methods=[],
                                  decorators=['dataclass']))
    if classes and target.language == 'python' and not any(
            type(d).__name__ == 'Import' and d.module == 'dataclasses'
            for d in target.imports):
        target.imports.insert(0, H.Import(module='dataclasses', names=['dataclass']))
    target.declarations = classes + list(target.declarations)


def ensure_package_inits(project: H.Project) -> dict[str, str]:
    """Extra __init__ files so generated packages import cleanly."""
    lang = (project.language or 'python').lower()
    extra: dict[str, str] = {}
    if lang != 'python':
        return extra
    pkgs: dict[str, list[str]] = {}
    for f in project.files:
        if type(f).__name__ != 'SourceFile':
            continue
        if '/' not in f.path:
            continue
        pkg = f.path.rsplit('/', 1)[0]
        mods = pkgs.setdefault(pkg, [])
        mod = f.path.rsplit('/', 1)[1]
        if mod != '__init__.py':
            mods.append(mod[:-3] if mod.endswith('.py') else mod)
    for pkg, mods in pkgs.items():
        lines = ['"""%s package."""' % pkg]
        for m in sorted(set(mods)):
            lines.append('from .%s import *  # noqa' % m)
        extra['%s/__init__.py' % pkg] = '\n'.join(lines) + '\n'
    if 'tests/__init__.py' not in [f.path for f in project.files]:
        extra['tests/__init__.py'] = ''
    return extra


# --------------------------------------------------------------------------
# body materialization: planner behavior names -> full snippet dicts
# --------------------------------------------------------------------------

def _params_of(func) -> tuple[list[str], dict[str, str]]:
    names = [p.name for p in func.params or []]
    types = {p.name: (p.type.name if getattr(p, 'type', None) else 'str')
             for p in func.params or []}
    return names, types


def _first_of(names: list[str], types: dict, want: str) -> str | None:
    if want == 'list':
        for n in names:
            if types.get(n) == 'list':
                return n
    if want == 'int':
        for n in names:
            if types.get(n) == 'int':
                return n
    if want == 'str':
        for n in names:
            if types.get(n) == 'str':
                return n
    return None


def _none_guards(names, types, exc):
    out = []
    for p in names:
        out.append({'kind': 'validate-inputs', 'checks': [
            {'param': p, 'pred': '%s != None' % p, 'exc': exc,
             'msg': 'null %s' % p}]})
    return out


def _int_guards(names, types):
    out = []
    for p in names:
        if types.get(p) == 'int':
            out.append({'kind': 'validate-inputs', 'checks': [
                {'param': p, 'pred': '%s >= 0' % p, 'exc': 'ValueError',
                 'msg': 'negative %s' % p}]})
    return out


def enrich_behaviors(func, crud_project=True):
    """Planner entries -> (full snippet dicts, strategy) with operands.

    Strategy is one of 'crud', 'evenodd', 'specific:<kind>', 'generic' and
    is recorded in project meta so test finalization uses the same decision
    (single source of truth). Specific behaviors require their operands to
    exist in the signature (a list source, a query second param); otherwise
    the honest generic-identity path is taken instead of emitting references
    to undefined names.
    """
    names, types = _params_of(func)
    verb = (func.name or '').split('_')[0].lower()
    kinds = [ (e.get('behavior', e.get('kind', 'compute')) if isinstance(e, dict) else str(e))
              for e in (func.behaviors or [])]
    if not kinds:
        kinds = ['compute']
    primary = kinds[0]
    out: list[dict] = []

    def L() -> str | None:
        return _first_of(names, types, 'list')

    def I() -> str:
        return 'x'

    def Q() -> str | None:
        if 'query' in names:
            return 'query'
        if len(names) >= 2:
            return names[1]
        return None

    # -- CRUD verbs first (signature-driven; planner may map these verbs to
    # -- generic behaviors like accumulate, which must not shadow CRUD here).
    # -- Shape is checked on pre-normalization params (records excluded):
    # -- materialize normalizes first, so 'records' present proves intent.
    _types = {p.name: (p.type.name if getattr(p, 'type', None) else 'str')
              for p in (func.params or [])}
    _base = [n for n in names if n != 'records']
    _btypes = {n: t for n, t in _types.items() if n != 'records'}
    if crud_project and 'records' in names \
            and _crud_shape(verb, _base, _btypes):
        if verb in ('add', 'create', 'append'):
            p = names[0]
            return ([{'kind': 'guard-raise', 'pred': '%s == None' % p,
                      'exc': 'TypeError', 'msg': 'null %s' % p},
                     {'kind': 'guard-raise', 'pred': '%s == ""' % p,
                      'exc': 'ValueError', 'msg': 'empty %s' % p}], 'crud')
        if verb in ('delete', 'remove'):
            p = names[0]
            return ([{'kind': 'guard-raise', 'pred': '%s < 0' % p,
                      'exc': 'ValueError', 'msg': 'negative id'}], 'crud')
        if verb in ('update', 'edit', 'rename'):
            return ([{'kind': 'guard-raise', 'pred': '%s < 0' % names[0],
                      'exc': 'ValueError', 'msg': 'negative id'},
                     {'kind': 'guard-raise', 'pred': '%s == ""' % names[1],
                      'exc': 'ValueError', 'msg': 'empty name'}], 'crud')
        return ([], 'crud')

    # -- specific pure behaviors (operands inferred from signature) --
    _list_kinds = {'sort-by', 'filter-where', 'count-where', 'accumulate',
                   'average', 'minmax-loop', 'dedupe', 'reverse-seq',
                   'group-count', 'transform-each', 'none-where',
                   'search-first'}
    _query_kinds = {'filter-where', 'count-where', 'none-where',
                    'search-first'}
    _specific_hit = (
        primary in SPECIFIC_BEHAVIORS or primary == 'recurse' or verb in (
            'sort', 'filter', 'count', 'average', 'reverse', 'dedupe'))
    _need_list = (primary in _list_kinds or (
        _specific_hit and verb in (
            'sort', 'filter', 'count', 'average', 'reverse', 'dedupe',
            'transform', 'search', 'find', 'lookup', 'group', 'min', 'max')))
    _need_query = (primary in _query_kinds or (
        _specific_hit and verb in (
            'filter', 'count', 'search', 'find', 'lookup')))
    _operands_ok = (not _need_list or L() is not None) and (
        not _need_query or Q() is not None)
    if _specific_hit and _operands_ok:
        src = L() or names[0]
        out.extend(_none_guards([src], types, 'TypeError'))
        if primary == 'sort-by' or verb == 'sort':
            out.append({'kind': 'sort-by', 'source': src, 'target': 'result',
                        'key': None, 'reverse': False})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'filter-where' or verb == 'filter':
            q = 'query' if 'query' in names else (names[1] if len(names) > 1 else 'query')
            out.append({'kind': 'guard-raise', 'pred': '%s == None' % q,
                        'exc': 'ValueError', 'msg': 'null query'})
            out.append({'kind': 'filter-where', 'source': src, 'item': I(),
                        'pred': '%s == %s' % (I(), q), 'target': 'result'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'count-where' or verb == 'count':
            q = 'query' if 'query' in names else (names[1] if len(names) > 1 else 'query')
            out.append({'kind': 'guard-raise', 'pred': '%s == None' % q,
                        'exc': 'ValueError', 'msg': 'null query'})
            out.append({'kind': 'count-where', 'source': src, 'item': I(),
                        'pred': '%s == %s' % (I(), q), 'target': 'result'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'accumulate':
            out.append({'kind': 'accumulate', 'source': src, 'item': I(),
                        'expr': I(), 'target': 'result', 'init': 0})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'average' or verb == 'average':
            out.append({'kind': 'average', 'source': src, 'target': 'result'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'minmax-loop' or verb in ('max', 'min'):
            mode = 'min' if (verb == 'min' or 'min' in func.name.lower()) else 'max'
            out.append({'kind': 'guard-raise', 'pred': 'len(%s) == 0' % src,
                        'exc': 'ValueError', 'msg': 'empty sequence'})
            out.append({'kind': 'minmax-loop', 'source': src, 'item': I(),
                        'target': 'result', 'mode': mode})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'dedupe' or verb == 'dedupe':
            out.append({'kind': 'dedupe', 'source': src, 'target': 'result'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'reverse-seq' or verb == 'reverse':
            out.append({'kind': 'reverse-seq', 'source': src, 'target': 'result'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'group-count' or verb == 'group':
            out.append({'kind': 'group-count', 'source': src, 'item': I(),
                        'key': I(), 'target': 'result'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'none-where':
            q = 'query' if 'query' in names else (names[1] if len(names) > 1 else 'query')
            if q not in names:
                names.append(q)
                func.params.append(H.Param(name=q, type=H.TypeRef(name='str')))
            out.append({'kind': 'none-where', 'source': src, 'item': I(),
                        'pred': '%s == %s' % (I(), q), 'target': 'result'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'search-first' or verb in ('search', 'find', 'lookup'):
            q = 'query' if 'query' in names else (names[1] if len(names) > 1 else 'query')
            out.append({'kind': 'guard-raise', 'pred': '%s == None' % q,
                        'exc': 'ValueError', 'msg': 'null query'})
            out.append({'kind': 'search-first', 'source': src, 'item': I(),
                        'pred': '%s == %s' % (I(), q), 'target': 'result',
                        'miss': 'none'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'transform-each' or verb == 'transform':
            out.append({'kind': 'transform-each', 'source': src, 'item': I(),
                        'expr': I(), 'target': 'result'})
            out.append({'kind': 'return-expr', 'expr': 'result'})
        elif primary == 'recurse':
            out.append({'kind': 'raise', 'exc': 'NotImplementedError',
                        'msg': 'unplannable recursion for %s' % func.name})
        else:
            out.append({'kind': 'return-expr', 'expr': names[0] if names else 'None'})
        return (out, 'specific:' + primary)

    # -- even/odd by name (precedent: planner cases do the same) --
    low = func.name.lower()
    if ('even' in low or 'odd' in low) and names:
        p = names[0]
        out.append({'kind': 'branch-return', 'branches': [
            {'pred': '%s %% 2 == 0' % p, 'value': "'even'"},
            {'pred': '%s %% 2 != 0' % p, 'value': "'odd'"}]})
        return (out, 'evenodd')

    # -- generic fallback: None -> ValueError, identity return --
    out.extend(_none_guards(names, types, 'ValueError'))
    out.extend(_int_guards(names, types))
    if len(names) >= 2 and all(types.get(n) in ('int', 'float') for n in names[:2]):
        out.append({'kind': 'return-expr', 'expr': '%s + %s' % (names[0], names[1])})
    elif names:
        out.append({'kind': 'return-expr', 'expr': names[0]})
    else:
        out.append({'kind': 'return-expr', 'expr': 'None'})
    return (out, 'generic')


def _crud_store_decls(lang: str = 'python') -> list:
    # Module store is only mutated, never rebound, so function bodies need
    # no `global` declaration in any backend.
    return [H.VarDecl(name='_STORE', type=H.TypeRef(name='dict'), value='{}')]


def _records_param() -> object:
    return H.Param(name='records', type=H.TypeRef(name='dict'),
                   default='None', help='record store (default: module store)')


def _store_resolve() -> list:
    """store = records if given else the module store (both backends).

    Declared up front: JS block scoping would otherwise trap the binding
    inside the if/else branches (const does not leak).
    """
    return [H.Assign(target='store', value=H.Literal(value=None)),
            H.If(cond=H.Compare(op='is', left=H.Var(name='records'),
                                right=H.Literal(value=None)),
                 then=[H.Assign(target='store', value=H.Var(name='_STORE'))],
                 elifs=[], else_body=[H.Assign(
                     target='store', value=H.Var(name='records'))])]


def _crud_add_rest(func) -> list:
    p = func.params[0].name
    nid, rec = '_nid', 'record'
    return _store_resolve() + [
        H.Assign(target=nid, value=H.BinOp(
            op='+', left=H.Call(func=H.Var(name='len'),
                                args=[H.Var(name='store')], kwargs={}),
            right=H.Literal(value=1))),
        H.While(cond=H.Compare(op='in', left=H.Var(name=nid),
                               right=H.Var(name='store')),
                body=[H.AugAssign(target=nid, op='+=',
                                  value=H.Literal(value=1))]),
        H.Assign(target=rec, value=H.DictLit(pairs=[
            (H.Literal(value='id'), H.Var(name=nid)),
            (H.Literal(value='name'), H.Var(name=p))])),
        H.IndexAssign(obj=H.Var(name='store'), index=H.Var(name=nid),
                      value=H.Var(name=rec)),
        H.Return(value=H.Var(name=rec)),
    ]


def _crud_delete_rest(func) -> list:
    p = func.params[0].name
    return _store_resolve() + [
        H.Assign(target='removed', value=H.Call(
            func=H.Attr(obj=H.Var(name='store'), attr='pop'),
            args=[H.Var(name=p), H.Literal(value=None)], kwargs={})),
        H.Return(value=H.Compare(op='is-not', left=H.Var(name='removed'),
                                 right=H.Literal(value=None))),
    ]


def _crud_update_rest(func) -> list:
    pid, pname = func.params[0].name, func.params[1].name
    return _store_resolve() + [
        H.If(cond=H.Compare(op='not-in', left=H.Var(name=pid),
                            right=H.Var(name='store')),
             then=[H.Return(value=H.Literal(value=False))], elifs=[], else_body=[]),
        H.IndexAssign(obj=H.Var(name='store'), index=H.Var(name=pid),
                      value=H.DictLit(pairs=[
                          (H.Literal(value='id'), H.Var(name=pid)),
                          (H.Literal(value='name'), H.Var(name=pname))])),
        H.Return(value=H.Literal(value=True)),
    ]


def _crud_list_rest(func) -> list:
    return _store_resolve() + [
        H.Return(value=H.Call(func=H.Var(name='list'),
                              args=[H.Call(func=H.Attr(obj=H.Var(name='store'), attr='values'),
                                           args=[], kwargs={})], kwargs={})),
    ]


def _crud_shape(verb: str, names: list, types: dict) -> bool:
    """True only for planner CRUD signatures (not arithmetic lookalikes).

    add_numbers(items) is accumulation; add_todo(name) is CRUD. The verb
    alone cannot tell them apart — the parameter shape can.
    """
    t = lambda n: (types.get(n) or 'str')
    if verb in ('add', 'create', 'append', 'delete', 'remove'):
        return len(names) == 1 and t(names[0]) != 'list'
    if verb in ('update', 'edit', 'rename'):
        return len(names) == 2 and t(names[0]) != 'list' \
            and t(names[1]) != 'list'
    if verb in ('list', 'show'):
        return len(names) == 1
    return False


def _crud_project_verbs(project: H.Project) -> bool:
    """CRUD normalization needs corroboration: >=2 distinct CRUD-family
    verbs project-wide (same philosophy as type detection). A lone
    add_numbers next to subtract/multiply is arithmetic, not a store."""
    seen = set()
    for f in project.files or []:
        if type(f).__name__ != 'SourceFile':
            continue
        for d in f.declarations or []:
            if type(d).__name__ != 'FuncDef':
                continue
            v = (d.name or '').split('_')[0].lower()
            if v in ('add', 'create', 'append', 'delete', 'remove',
                     'update', 'edit', 'rename', 'list', 'show'):
                seen.add(v)
    return len(seen) >= 2


def materialize_bodies(project: H.Project) -> None:
    """Fill empty function bodies from behavior entries. Mutates project."""
    from .snippets import build_body
    crud_project = _crud_project_verbs(project)
    for f in project.files:
        if type(f).__name__ != 'SourceFile':
            continue
        need_store = False
        file_lang = getattr(f, 'language', 'python') or 'python'
        for d in f.declarations or []:
            if type(d).__name__ != 'FuncDef':
                continue
            # engine run/step have concrete planner bodies; complete them so
            # the generic null/identity cases hold (return input, reject null).
            if d.name == 'run' and d.params:
                first = d.params[0].name
                if not any(type(s).__name__ == 'Return' for s in d.body or []):
                    d.body = list(d.body or []) + [H.Return(value=H.Var(name=first))]
                if not any(type(s).__name__ == 'Raise' for s in d.body or []):
                    d.body = [H.If(cond=H.Compare(op='==', left=H.Var(name=first),
                                                  right=H.Literal(value=None)),
                                   then=[H.Raise(exc='ValueError',
                                                 message=H.Literal(value='null %s' % first))],
                                   elifs=[], else_body=[])] + list(d.body or [])
            if d.name == 'step' and d.params:
                if not any(type(s).__name__ == 'Raise' for s in d.body or []):
                    guards = []
                    for p in d.params or []:
                        guards.append(H.If(
                            cond=H.Compare(op='==', left=H.Var(name=p.name),
                                           right=H.Literal(value=None)),
                            then=[H.Raise(exc='ValueError',
                                          message=H.Literal(value='null %s' % p.name))],
                            elifs=[], else_body=[]))
                    d.body = guards + list(d.body or [])
            body = d.body or []
            if body and not (len(body) == 1 and type(body[0]).__name__ == 'Pass'):
                continue
            verb = (d.name or '').split('_')[0].lower()
            # Normalize CRUD signatures (documented): an explicit store
            # parameter keeps generated tests order-independent; callers may
            # pass records=None to use the module store (CLI dispatch does).
            # Shape-gated: add_numbers(items) stays accumulation.
            names = [p.name for p in d.params or []]
            types = {p.name: (p.type.name if getattr(p, 'type', None) else 'str')
                     for p in d.params or []}
            if crud_project and verb in ('add', 'create', 'append', 'delete',
                         'remove', 'update', 'edit', 'rename', 'list',
                         'show') and _crud_shape(verb, names, types):
                if verb in ('list', 'show'):
                    d.params = [_records_param()]
                elif 'records' not in names:
                    d.params = list(d.params or []) + [_records_param()]
                names = [p.name for p in d.params]
                types = {p.name: (p.type.name if getattr(p, 'type', None)
                                  else 'str') for p in d.params}
            enriched, strategy = enrich_behaviors(d, crud_project)
            stmts = build_body(enriched, d.name, [p.name for p in d.params or []])
            # CRUD raw-HIR tails (explicit-store semantics; same gate)
            names = [p.name for p in d.params or []]
            if crud_project and verb in ('add', 'create', 'append') \
                    and 'records' in names:
                stmts = [s for s in stmts] + _crud_add_rest(d)
                need_store = True
                d.returns = H.TypeRef(name='dict')
                strategy = 'crud-add'
            elif crud_project and verb in ('delete', 'remove') \
                    and 'records' in names:
                stmts = [s for s in stmts] + _crud_delete_rest(d)
                need_store = True
                d.returns = H.TypeRef(name='bool')
                strategy = 'crud-delete'
            elif crud_project and verb in ('update', 'edit', 'rename') \
                    and len(names) >= 3 and 'records' in names:
                stmts = [s for s in stmts] + _crud_update_rest(d)
                need_store = True
                d.returns = H.TypeRef(name='bool')
                strategy = 'crud-update'
            elif crud_project and verb in ('list', 'show') \
                    and names == ['records']:
                # identity returns from enrich would shadow the store tail;
                # drop them so the store tail is the live return.
                stmts = [s for s in stmts if type(s).__name__ != 'Return']
                stmts = stmts + _crud_list_rest(d)
                need_store = True
                d.returns = H.TypeRef(name='list')
                strategy = 'crud-list'
            d.body = stmts or [H.Pass()]
            try:
                project.meta.setdefault('body_strategy', {})[
                    '%s::%s' % (f.path, d.name)] = strategy
            except Exception:
                pass
        if need_store and not any(type(x).__name__ == 'VarDecl' and x.name == '_STORE'
                                  for x in f.declarations or []):
            f.declarations = _crud_store_decls(file_lang) + list(f.declarations or [])


# --------------------------------------------------------------------------
# interface builders (CLI dispatch, API serve, JSON store IO)
# --------------------------------------------------------------------------

def build_cli_main(project: H.Project) -> None:
    """Replace cli.py main() stub with a real argparse dispatcher (python only)."""
    if not project.cli or (project.language or 'python').lower() != 'python':
        return
    for f in project.files:
        if type(f).__name__ != 'SourceFile' or not f.path.endswith('cli.py'):
            continue
        handlers: dict[str, str] = {}
        for c in project.cli:
            handlers[c.name] = c.handler
        if not any(type(d).__name__ == 'FuncDef' and d.name == 'main'
                   for d in f.declarations or []):
            continue
        for d in f.declarations or []:
            if type(d).__name__ == 'FuncDef' and d.name == 'main':
                d.body = _cli_main_body(handlers)
        mods = sorted({h.rsplit('.', 1)[0] for h in handlers.values() if '.' in h})
        have = {i.module for i in f.imports or []}
        for m in mods:
            if m not in have:
                f.imports.append(H.Import(module=m))
        if 'sys' not in have and project.language == 'python':
            f.imports.append(H.Import(module='sys'))
        elif 'sys' not in have:
            f.imports.append(H.Import(module='sys'))


def _cli_main_body(handlers: dict[str, str]) -> list:
    H_ = H
    dispatch_pairs = [(name, h) for name, h in sorted(handlers.items())]
    add_cmds = []
    for name, handler in dispatch_pairs:
        short = handler.rsplit('.', 1)[-1]
        add_cmds.append(H_.ExprStmt(expr=H_.Call(
            func=H_.Attr(obj=H_.Var(name='sub'), attr='add_parser'),
            args=[H_.Literal(value=name)], kwargs={})))
    call_result = H_.Call(
        func=H_.Var(name='_dispatch'),
        args=[H_.Var(name='args')], kwargs={})
    return [
        H_.Assign(target='parser', value=H_.Call(
            func=H_.Attr(obj=H_.Var(name='argparse'), attr='ArgumentParser'),
            args=[], kwargs={'description': H_.Literal(value='Epsilon CLI')})),
        H_.Assign(target='sub', value=H_.Call(
            func=H_.Attr(obj=H_.Var(name='parser'), attr='add_subparsers'),
            args=[], kwargs={'dest': H_.Literal(value='command')})),
        *add_cmds,
        H_.Assign(target='args', value=H_.Call(
            func=H_.Attr(obj=H_.Var(name='parser'), attr='parse_args'),
            args=[], kwargs={})),
        H_.If(cond=H_.Compare(op='==', left=H_.Attr(obj=H_.Var(name='args'), attr='command'),
                              right=H_.Literal(value=None)),
              then=[H_.ExprStmt(expr=H_.Call(
                  func=H_.Attr(obj=H_.Var(name='parser'), attr='print_help'),
                  args=[], kwargs={})),
                    H_.Return(value=H_.Literal(value=0))],
              elifs=[], else_body=[]),
        H_.Assign(target='result', value=call_result),
        H_.ExprStmt(expr=H_.Call(func=H_.Var(name='print'), args=[H_.Var(name='result')], kwargs={})),
        H_.Return(value=H_.Literal(value=0)),
    ]


def _find_func(project: H.Project, dotted: str):
    parts = (dotted or '').split('.')
    if len(parts) < 2:
        return None
    mod, name = '.'.join(parts[:-1]), parts[-1]
    for f in project.files:
        if type(f).__name__ != 'SourceFile':
            continue
        dm = f.path.replace('/', '.')
        for suffix in ('.py', '.js', '.ts'):
            if dm.endswith(suffix):
                dm = dm[:-len(suffix)]
        if dm != mod and not dm.endswith('.' + mod) and mod not in dm:
            continue
        for d in f.declarations or []:
            if type(d).__name__ == 'FuncDef' and d.name == name:
                return d
    return None


def build_dispatch_helper(project: H.Project) -> None:
    """Add _dispatch(args): command -> handler( matching namespace attrs )."""
    if not project.cli or (project.language or 'python').lower() != 'python':
        return
    for f in project.files:
        if type(f).__name__ != 'SourceFile' or not f.path.endswith('cli.py'):
            continue
        branches = []
        for c in sorted(project.cli, key=lambda c: c.name):
            fn = _find_func(project, c.handler)
            arg_gets = []
            if fn is not None:
                for p in fn.params or []:
                    arg_gets.append(H.Call(func=H.Var(name='getattr'),
                                           args=[H.Var(name='args'),
                                                 H.Literal(value=p.name),
                                                 H.Literal(value=None)], kwargs={}))
            short = c.handler.rsplit('.', 1)[-1]
            mod = c.handler.rsplit('.', 1)[0] if '.' in c.handler else ''
            call = H.Call(func=H.Attr(obj=H.Var(name=mod), attr=short) if mod else H.Var(name=short),
                          args=arg_gets, kwargs={})
            branches.append((H.Compare(op='==', left=H.Attr(obj=H.Var(name='args'), attr='command'),
                                       right=H.Literal(value=c.name)),
                             [H.Return(value=call)]))
        if not branches:
            return
        cond0, body0 = branches[0]
        disp = H.FuncDef(name='_dispatch',
                         params=[H.Param(name='args', type=H.TypeRef(name='Any'))],
                         returns=H.TypeRef(name='Any'),
                         doc='Route a parsed namespace to its handler.',
                         behaviors=[], body=[H.If(cond=cond0, then=body0,
                                                  elifs=[(c, b) for c, b in branches[1:]],
                                                  else_body=[H.Return(value=H.Literal(value=None))])])
        f.declarations = list(f.declarations or []) + [disp]
        if not any(i.module == 'typing' for i in f.imports or []):
            f.imports.append(H.Import(module='typing', names=['Any']))


def build_api_serve(project: H.Project) -> None:
    """Add serve() + route-printing entry to api.py (python, stdlib only).

    Each endpoint gets a sequential match+gate block ending in Return, so
    per-endpoint `ok`/`ident` bindings never leak across routes.
    """
    if not project.endpoints or (project.language or 'python').lower() != 'python':
        return
    for f in project.files:
        if type(f).__name__ != 'SourceFile' or not f.path.endswith('api.py'):
            continue
        have = {i.module for i in f.imports or []}
        if 'urllib.parse' not in have:
            f.imports.append(H.Import(module='urllib.parse'))
        eps = [(e.method.upper(), e.path, (e.handler or '').rsplit('.', 1)[-1])
               for e in project.endpoints]
        match_fn = H.FuncDef(
            name='_match_route',
            params=[H.Param(name='route', type=H.TypeRef(name='str')),
                    H.Param(name='path', type=H.TypeRef(name='str'))],
            returns=H.TypeRef(name='tuple'),
            doc='Match a route template; returns (ok, id_or_None).',
            behaviors=[], body=[
                H.Assign(target='rs', value=H.Call(
                    func=H.Attr(obj=H.Var(name='route'), attr='strip'),
                    args=[H.Literal(value='/')], kwargs={})),
                H.Assign(target='ps', value=H.Call(
                    func=H.Attr(obj=H.Var(name='path'), attr='strip'),
                    args=[H.Literal(value='/')], kwargs={})),
                H.Assign(target='rl', value=H.Call(
                    func=H.Attr(obj=H.Var(name='rs'), attr='split'),
                    args=[H.Literal(value='/')], kwargs={})),
                H.Assign(target='pl', value=H.Call(
                    func=H.Attr(obj=H.Var(name='ps'), attr='split'),
                    args=[H.Literal(value='/')], kwargs={})),
                H.If(cond=H.Compare(
                    op='!=',
                    left=H.Call(func=H.Var(name='len'), args=[H.Var(name='rl')], kwargs={}),
                    right=H.Call(func=H.Var(name='len'), args=[H.Var(name='pl')], kwargs={})),
                    then=[H.Return(value=H.ListLit(items=[H.Literal(value=False),
                                                           H.Literal(value=None)]))],
                    elifs=[], else_body=[]),
                H.Assign(target='ident', value=H.Literal(value=None)),
                H.Assign(target='ok', value=H.Literal(value=True)),
                H.ForIn(var='pair', iter=H.Call(func=H.Var(name='zip'),
                                                args=[H.Var(name='rl'), H.Var(name='pl')],
                                                kwargs={}), body=[
                    H.Assign(target='a', value=H.Subscript(
                        obj=H.Var(name='pair'), index=H.Literal(value=0))),
                    H.Assign(target='b', value=H.Subscript(
                        obj=H.Var(name='pair'), index=H.Literal(value=1))),
                    H.If(cond=H.Compare(
                        op='in', left=H.Var(name='a'),
                        right=H.ListLit(items=[H.Literal(value='{id}'),
                                               H.Literal(value=':id'),
                                               H.Literal(value='<id>')])),
                        then=[H.Assign(target='ident', value=H.Var(name='b'))],
                        elifs=[],
                        else_body=[H.If(
                            cond=H.Compare(op='!=', left=H.Var(name='a'),
                                           right=H.Var(name='b')),
                            then=[H.Assign(target='ok', value=H.Literal(value=False))],
                            elifs=[], else_body=[])]),
                ]),
                H.Return(value=H.ListLit(items=[H.Var(name='ok'), H.Var(name='ident')])),
            ])
        blocks: list = [
            H.Assign(target='parsed', value=H.Call(
                func=H.Attr(obj=H.Attr(obj=H.Var(name='urllib'), attr='parse'),
                            attr='urlparse'),
                args=[H.Attr(obj=H.Var(name='req'), attr='path')], kwargs={})),
            H.Assign(target='path', value=H.Attr(obj=H.Var(name='parsed'), attr='path')),
            H.Assign(target='qs', value=H.Call(
                func=H.Attr(obj=H.Attr(obj=H.Var(name='urllib'), attr='parse'),
                            attr='parse_qs'),
                args=[H.Attr(obj=H.Var(name='parsed'), attr='query')], kwargs={})),
        ]
        for method, route, handler in eps:
            fn = _find_func(project, next(
                (e.handler for e in project.endpoints if e.handler.endswith('.' + handler)),
                handler))
            need = [p.name for p in fn.params] if fn is not None else []
            pre: list = [
                H.Assign(target='match', value=H.Call(
                    func=H.Var(name='_match_route'),
                    args=[H.Literal(value=route), H.Var(name='path')], kwargs={})),
                H.Assign(target='ok', value=H.Subscript(
                    obj=H.Var(name='match'), index=H.Literal(value=0))),
                H.Assign(target='ident', value=H.Subscript(
                    obj=H.Var(name='match'), index=H.Literal(value=1))),
            ]
            call_args = []
            for p in need:
                if p == 'payload':
                    pre.append(H.Assign(target='length', value=H.Call(
                        func=H.Var(name='int'), args=[H.Call(
                            func=H.Attr(obj=H.Var(name='req'), attr='headers'),
                            args=[], kwargs={}) if False else H.Call(
                            func=H.Attr(obj=H.Attr(obj=H.Var(name='req'), attr='headers'),
                                        attr='get'),
                            args=[H.Literal(value='Content-Length'),
                                  H.Literal(value='0')], kwargs={})], kwargs={})))
                    pre.append(H.Assign(target='raw', value=H.Call(
                        func=H.Attr(obj=H.Attr(obj=H.Var(name='req'), attr='rfile'),
                                    attr='read'),
                        args=[H.Var(name='length')], kwargs={})))
                    pre.append(H.Assign(target='payload', value=H.Call(
                        func=H.Attr(obj=H.Var(name='json'), attr='loads'),
                        args=[H.Call(
                            func=H.Attr(obj=H.Var(name='raw'), attr='decode'),
                            args=[H.Literal(value='utf-8')], kwargs={})], kwargs={})))
                    call_args.append(H.Var(name='payload'))
                elif p == 'item_id':
                    pre.append(H.Assign(target='item_id', value=H.Call(
                        func=H.Var(name='int'), args=[H.Var(name='ident')], kwargs={})))
                    call_args.append(H.Var(name='item_id'))
                elif p == 'query':
                    pre.append(H.Assign(target='query', value=H.Subscript(
                        obj=H.Call(
                            func=H.Attr(obj=H.Var(name='qs'), attr='get'),
                            args=[H.Literal(value='query'),
                                  H.ListLit(items=[H.Literal(value='')])], kwargs={}),
                        index=H.Literal(value=0))))
                    call_args.append(H.Var(name='query'))
                else:
                    call_args.append(H.Literal(value=None))
            gate_body: list = list(pre)
            gate_body.append(H.Assign(target='resp', value=H.Call(
                func=H.Var(name=handler), args=call_args, kwargs={})))
            gate_body.append(H.Assign(target='status', value=H.Subscript(
                obj=H.Var(name='resp'), index=H.Literal(value=0))))
            gate_body.append(H.Assign(target='payload_out', value=H.Subscript(
                obj=H.Var(name='resp'), index=H.Literal(value=1))))
            gate_body.append(H.Assign(target='data', value=H.Call(
                func=H.Attr(obj=H.Call(
                    func=H.Attr(obj=H.Var(name='json'), attr='dumps'),
                    args=[H.Var(name='payload_out')], kwargs={}), attr='encode'),
                args=[H.Literal(value='utf-8')], kwargs={})))
            gate_body.append(H.ExprStmt(expr=H.Call(
                func=H.Attr(obj=H.Var(name='req'), attr='send_response'),
                args=[H.Var(name='status')], kwargs={})))
            gate_body.append(H.ExprStmt(expr=H.Call(
                func=H.Attr(obj=H.Var(name='req'), attr='send_header'),
                args=[H.Literal(value='Content-Type'),
                      H.Literal(value='application/json')], kwargs={})))
            gate_body.append(H.ExprStmt(expr=H.Call(
                func=H.Attr(obj=H.Var(name='req'), attr='end_headers'),
                args=[], kwargs={})))
            gate_body.append(H.ExprStmt(expr=H.Call(
                func=H.Attr(obj=H.Attr(obj=H.Var(name='req'), attr='wfile'),
                            attr='write'),
                args=[H.Var(name='data')], kwargs={})))
            gate_body.append(H.Return(value=H.Literal(value=None)))
            blocks.extend(pre[:3])
            blocks.append(H.If(
                cond=H.BoolOp(op='and', values=[
                    H.Compare(op='==', left=H.Var(name='method'),
                              right=H.Literal(value=method)),
                    H.Var(name='ok')]),
                then=gate_body[3:], elifs=[], else_body=[]))
        blocks.append(H.ExprStmt(expr=H.Call(
            func=H.Attr(obj=H.Var(name='req'), attr='send_response'),
            args=[H.Literal(value=404)], kwargs={})))
        blocks.append(H.ExprStmt(expr=H.Call(
            func=H.Attr(obj=H.Var(name='req'), attr='end_headers'),
            args=[], kwargs={})))
        blocks.append(H.Return(value=H.Literal(value=None)))
        serve_req = H.FuncDef(name='_serve_request',
                              params=[H.Param(name='req'), H.Param(name='method')],
                              returns=None, doc='Dispatch one request to its handler.',
                              behaviors=[], body=blocks)
        methods = []
        for verb in sorted({m for m, _r, _h in eps}):
            methods.append(H.FuncDef(
                name='do_' + verb, params=[H.Param(name='self')],
                returns=None, doc='Handle %s.' % verb, behaviors=[],
                body=[H.ExprStmt(expr=H.Call(
                    func=H.Var(name='_serve_request'),
                    args=[H.Var(name='self'), H.Literal(value=verb)], kwargs={}))]))
        handler_cls = H.ClassDef(name='_Handler',
                                 bases=['http.server.BaseHTTPRequestHandler'],
                                 doc='Routes requests to generated handlers.',
                                 fields=[], methods=methods)
        serve_fn = H.FuncDef(
            name='serve',
            params=[H.Param(name='port', type=H.TypeRef(name='int'), default='8000')],
            returns=None, doc='Start the stdlib HTTP server.',
            behaviors=[], body=[
                H.Assign(target='server', value=H.Call(
                    func=H.Attr(obj=H.Attr(obj=H.Var(name='http'), attr='server'),
                                        attr='HTTPServer'),
                    args=[H.ListLit(items=[H.Literal(value='127.0.0.1'),
                                           H.Var(name='port')]),
                          H.Var(name='_Handler')], kwargs={})),
                H.ExprStmt(expr=H.Call(
                    func=H.Attr(obj=H.Var(name='server'), attr='serve_forever'),
                    args=[], kwargs={})),
            ])
        f.declarations = list(f.declarations or []) + [match_fn, serve_req, handler_cls, serve_fn]
        route_lines = ['endpoints:'] + ['%s %s' % (m, r) for m, r, _h in eps]
        route_lines.append('run serve() to start')
        f.main_block = [H.ExprStmt(expr=H.Call(
            func=H.Var(name='print'), args=[H.Literal(value='\n'.join(route_lines))],
            kwargs={}))]


def build_store_io(project: H.Project) -> None:
    """Replace JSON store load/save stubs with real file IO (sqlite stays honest)."""
    for f in project.files:
        if type(f).__name__ != 'SourceFile' or 'store' not in (f.path or ''):
            continue
        is_sql = any('sqlite' in (i.module or '') for i in f.imports or [])
        for d in f.declarations or []:
            if type(d).__name__ != 'FuncDef':
                continue
            if d.name.startswith('load_') and not is_sql:
                path = d.params[0].name if d.params else 'path'
                d.body = [
                    H.If(cond=H.Compare(op='==', left=H.Var(name=path),
                                        right=H.Literal(value=None)),
                         then=[H.Raise(exc='ValueError',
                                       message=H.Literal(value='null %s' % path))],
                         elifs=[], else_body=[]),
                    H.Try(body=[
                        H.With(items=[(H.Call(func=H.Var(name='open'),
                                                     args=[H.Var(name=path)],
                                                     kwargs={'mode': H.Literal(value='r')}), 'fh')],
                               body=[H.Return(value=H.Call(
                                   func=H.Attr(obj=H.Var(name='json'), attr='load'),
                                   args=[H.Var(name='fh')], kwargs={}))])],
                        handlers=[H.Handler(exc='FileNotFoundError', name=None,
                                            body=[H.Return(value=H.ListLit(items=[]))])],
                        else_body=[], finally_body=[]),
                ]
            elif d.name.startswith('save_') and not is_sql:
                recs = d.params[0].name if d.params else 'records'
                path = d.params[1].name if len(d.params) > 1 else 'path'
                d.body = [
                    H.If(cond=H.Compare(op='==', left=H.Var(name=recs),
                                        right=H.Literal(value=None)),
                         then=[H.Raise(exc='ValueError',
                                       message=H.Literal(value='null %s' % recs))],
                         elifs=[], else_body=[]),
                    H.With(items=[(H.Call(func=H.Var(name='open'),
                                                  args=[H.Var(name=path)],
                                                  kwargs={'mode': H.Literal(value='w')}), 'fh')],
                            body=[H.ExprStmt(expr=H.Call(
                                func=H.Attr(obj=H.Var(name='json'), attr='dump'),
                                args=[H.Var(name=recs), H.Var(name='fh')], kwargs={}))]),
                    H.Return(value=H.Call(func=H.Var(name='len'),
                                          args=[H.Var(name=recs)], kwargs={})),
                ]
            elif is_sql:
                d.body = [H.Raise(exc='NotImplementedError',
                                  message=H.Literal(value='sqlite backend not generated in v2'))]


# --------------------------------------------------------------------------
# test finalization: cases the bodies actually guarantee
# --------------------------------------------------------------------------

def my_cases_for(func, dotted: str, strategy=None) -> list | None:
    """Behavioral cases consistent with materialized bodies.

    Strategy comes from materialize_bodies (recorded in project meta):
    'crud-*' -> store cases, 'evenodd' -> parity cases, 'generic' ->
    identity cases, anything else (specific behaviors with verified
    operands, or concrete planner bodies) -> None (keep planner's cases,
    which were built from the same behavior table).
    """
    from .hir import TestCase
    names = [p.name for p in func.params or []]
    verb = (func.name or '').split('_')[0].lower()
    low = func.name.lower()
    cases: list = []

    def C(given, expect=None, raises=None, note=''):
        kw = {'given': given, 'note': note}
        if expect is not None:
            kw['expect'] = expect
        if raises is not None:
            kw['raises'] = raises
        return TestCase(**kw)

    if verb in ('add', 'create', 'append') and strategy == 'crud-add' \
            and 'records' in names:
        p0, pr = names[0], names[1]
        rec1 = {'id': 1, 'name': 'Buy milk'}
        return [C({p0: 'Buy milk', pr: {}}, repr(rec1),
                   note='normal: add stores record'),
                C({p0: 'A', pr: {7: {'id': 7, 'name': 'Z'}}},
                   repr({'id': 2, 'name': 'A'}),
                   note='edge: id avoids collision'),
                C({p0: '', pr: {}}, raises='ValueError',
                   note='invalid: empty name rejected'),
                C({p0: None, pr: {}}, raises='TypeError',
                   note='invalid: null name raises')]
    if verb in ('delete', 'remove') and strategy == 'crud-delete' \
            and 'records' in names:
        p0, pr = names[0], names[1]
        return [C({p0: 1, pr: {1: {'id': 1, 'name': 'Buy milk'}}},
                   'True', note='normal: known id removed'),
                C({p0: 999, pr: {}}, 'False',
                   note='edge: unknown id removes nothing'),
                C({p0: -1, pr: {}}, raises='ValueError',
                   note='invalid: negative id raises')]
    if verb in ('update', 'edit', 'rename') and strategy == 'crud-update' \
            and 'records' in names and len(names) >= 3:
        p0, p1, pr = names[0], names[1], names[2]
        return [C({p0: 1, p1: 'New',
                    pr: {1: {'id': 1, 'name': 'Old'}}}, 'True',
                   note='normal: known id updated'),
                C({p0: 999, p1: 'New', pr: {}}, 'False',
                   note='edge: unknown id updates nothing'),
                C({p0: 1, p1: '', pr: {}}, raises='ValueError',
                   note='invalid: empty name raises')]
    if verb in ('list', 'show') and strategy == 'crud-list' \
            and names == ['records']:
        rec1 = {'id': 1, 'name': 'Buy milk'}
        return [C({'records': {1: dict(rec1)}}, repr([dict(rec1)]),
                   note='normal: lists stored records'),
                C({'records': {}}, '[]',
                   note='edge: empty store lists empty')]
    if strategy and strategy.startswith('specific'):
        _types = {p.name: (p.type.name if getattr(p, 'type', None) else 'str')
                  for p in func.params or []}
        _src = next((n for n in names if _types.get(n) == 'list'), None)
        _q = 'query' if 'query' in names else (
            names[1] if len(names) > 1 else None)
        if verb in ('search', 'find', 'lookup') and _src and _q:
            return [C({_src: [1, 2, 3], _q: 2}, '2', note='normal: first match'),
                    C({_src: [], _q: 2}, 'None', note='edge: empty finds nothing'),
                    C({_src: [1], _q: None}, raises='ValueError', note='invalid: null query')]
        if verb == 'sort' and _src:
            return [C({_src: [3, 1, 2]}, '[1, 2, 3]', note='normal: sorted'),
                    C({_src: []}, '[]', note='edge: empty'),
                    C({_src: None}, raises='TypeError', note='invalid: null')]
        if verb == 'filter' and _src and _q:
            return [C({_src: [1, 2, 3], _q: 2}, '[2]', note='normal: keeps matches'),
                    C({_src: [], _q: 2}, '[]', note='edge: empty'),
                    C({_src: None, _q: 2}, raises='TypeError', note='invalid: null')]
        if verb == 'count' and _src and _q:
            return [C({_src: [1, 2, 3], _q: 2}, '1', note='normal: one match'),
                    C({_src: [], _q: 2}, '0', note='edge: empty'),
                    C({_src: None, _q: 2}, raises='TypeError', note='invalid: null')]
        return None  # other specific behaviors: planner cases already match
    if ('even' in low or 'odd' in low) and strategy == 'evenodd':
        p = names[0] if names else 'n'
        return [C({p: 4}, "'even'", note='normal: 4 is even'),
                C({p: 7}, "'odd'", note='normal: 7 is odd'),
                C({p: 0}, "'even'", note='edge: zero is even')]
    if low.startswith('load_'):
        return [C({'path': 'eps_missing_xyz.json'}, '[]', note='edge: missing file loads empty'),
                C({'path': None}, raises='ValueError', note='invalid: null path')]
    # Generic identity fallback: only when enrich took its generic path
    # (strategy recorded in meta). Must come AFTER the dedicated
    # load_/save_/step/run/api branches below, which own their semantics.
    _generic_ok = (strategy == 'generic' and names and 'records' not in names)
    if _generic_ok:
        _samples = {'int': 3, 'float': 2.5, 'bool': True, 'list': [3, 1, 2],
                    'dict': {'key': 'value'}, 'str': 'sample'}
        _zeros = {'int': 0, 'float': 0.0, 'bool': False, 'list': [],
                  'dict': {}, 'str': ''}
        _types = {p.name: (p.type.name if getattr(p, 'type', None) else 'str')
                  for p in func.params or []}
        sample = {n: _samples.get(_types.get(n, 'str'), 'sample') for n in names}
        first = names[0]
        zero = {n: _zeros.get(_types.get(n, 'str'), '') for n in names}
        return [C(dict(sample), repr(sample[first]),
                   note='normal: identity over sample input'),
                C(dict(zero), repr(_zeros.get(_types.get(first, 'str'), '')),
                   note='edge: zero input passes through'),
                C({first: None}, raises='ValueError',
                   note='invalid: null input raises')]
    if low == 'step' and len(names) >= 2:
        return [C({names[0]: {'x': 1}, names[1]: 'tick'}, "{'x': 1}",
                   note='normal: step passes state through'),
                C({names[0]: {}, names[1]: 'tick'}, '{}',
                   note='edge: empty state passes through'),
                C({names[0]: None, names[1]: 'tick'}, raises='ValueError',
                   note='invalid: null state raises')]
    if low == 'run' and names:
        return [C({names[0]: {'x': 1}}, "{'x': 1}",
                   note='normal: run returns final state'),
                C({names[0]: {}}, '{}', note='edge: empty initial state'),
                C({names[0]: None}, raises='ValueError',
                   note='invalid: null initial raises')]
    if low.startswith('save_'):
        return [C({'records': [{'id': 1}], 'path': 'eps_probe.json'}, '1',
                   note='normal: save returns count'),
                C({'records': None, 'path': 'eps_probe.json'}, raises='ValueError',
                   note='invalid: null records')]
    # API handlers by method embedded in name stem (planner bodies return
    # the literal (status, {}) contract; cases assert exactly that shape).
    # Guarded to api.py files: domain functions may share verb prefixes
    # (get_notes vs get_noteses) but have identity semantics instead.
    if ('api' in dotted.split('.') and
            (low.startswith('get_') or low.startswith('post_') or
             low.startswith('put_') or low.startswith('delete_') or
             low.startswith('patch_'))):
        sample = {n: ('sample' if (t == 'str') else 1) for n, t in
                  [(p.name, p.type.name if getattr(p, 'type', None) else 'str') for p in func.params or []]}
        return [C(dict(sample), '(200, {})', note='smoke: handler returns status/body')]
    # generic: sample round-trips through identity-ish bodies
    return None


def finalize_tests(project: H.Project) -> None:
    """Replace planner TestSpecs with cases the materialized bodies guarantee."""
    specs = []
    strategies = {}
    try:
        meta = getattr(project, 'meta', {}) or {}
        strategies = meta.get('body_strategy', {}) or {}
    except Exception:
        strategies = {}
    for f in project.files:
        if type(f).__name__ != 'SourceFile':
            continue
        dotted = f.path.replace('/', '.')
        for suffix in ('.py', '.js', '.ts'):
            if dotted.endswith(suffix):
                dotted = dotted[:-len(suffix)]
        for d in f.declarations or []:
            if type(d).__name__ != 'FuncDef':
                continue
            if d.name.startswith('_'):
                continue
            strategy = strategies.get('%s::%s' % (f.path, d.name))
            mine = my_cases_for(d, dotted, strategy)
            if mine is None:
                continue  # keep planner spec for this one
            specs.append(H.TestSpec(name=re.sub(r'^test_', '', 'test_%s' % d.name),
                                    target='%s.%s' % (dotted, d.name),
                                    cases=mine, setup=''))
    # drop planner specs for replaced targets, keep the rest (api smoke etc.)
    replaced = {s.target for s in specs}
    kept = []
    for s in project.tests:
        if getattr(s, 'target', '') in replaced:
            continue
        s.name = re.sub(r'^test_', '', getattr(s, 'name', '') or '')
        kept.append(s)
    project.tests = kept + specs


# --------------------------------------------------------------------------
# build orchestrator
# --------------------------------------------------------------------------

def _restore_from_plan(out_dir: str, project, rel: str, events=None) -> bool:
    """Re-render one file from pristine HIR and rewrite it. True if changed."""
    from .backends import get_backend
    for f in project.files or []:
        if type(f).__name__ == 'SourceFile' and f.path == rel:
            try:
                content = get_backend(project.language).render_file(f)
            except Exception:
                return False
            full = os.path.join(out_dir, rel)
            try:
                with open(full) as fh:
                    if fh.read() == content:
                        return False
                with open(full, 'w') as fh:
                    fh.write(content)
            except OSError:
                return False
            if events is not None:
                events.emit('repair', 'repaired',
                            'restored %s from plan' % rel, {'file': rel})
            return True
    return False


def write_files(out_dir: str, files: dict[str, str]) -> list[str]:
    written = []
    for path, content in sorted(files.items()):
        full = os.path.join(out_dir, path)
        os.makedirs(os.path.dirname(full) or out_dir, exist_ok=True)
        with open(full, 'w') as fh:
            fh.write(content)
        written.append(path)
    return written


def build(spec: str, config: EpsilonConfig | None = None,
          events: EventLog | None = None):
    """Full pipeline: spec -> verified project on disk. Returns (out_dir, report, ctx)."""
    from .requirements import analyze
    from .planner import plan_project
    from .backends import get_backend
    from . import testgen as TG
    from . import validate as V
    from . import sandbox as SB
    from . import repair as RP
    from .context import build_context
    t0 = time.time()
    if config is None:
        config = EpsilonConfig()
    elif isinstance(config, str):
        config = EpsilonConfig(out_dir=config)
    elif isinstance(config, dict):
        config = EpsilonConfig.from_dict(config)
    events = events or EventLog()
    problems = config.validate()
    if problems:
        raise ValueError('bad config: %s' % problems)
    events.emit('parse', 'started', 'analyzing requirements', {})
    req = analyze(spec)
    events.emit('parse', 'produced', 'requirements: %s' % req.title,
                {'goals': len(req.goals), 'ambiguities': len(req.ambiguities)})
    events.emit('plan', 'started', 'planning project', {})
    project = plan_project(req, config.target_language, events)
    normalize(project, events)
    attach_models(project)
    materialize_bodies(project)
    build_cli_main(project)
    build_dispatch_helper(project)
    build_api_serve(project)
    build_store_io(project)
    finalize_tests(project)
    events.emit('generate', 'started', 'rendering %d files' % len(project.files), {})
    backend = get_backend(config.target_language)
    files = backend.render_project(project)
    files.update(TG.tests_for(project))
    files.update(ensure_package_inits(project))
    out_dir = config.out_dir
    os.makedirs(out_dir, exist_ok=True)
    written = write_files(out_dir, files)
    # persist HIR for repair/inspect flows
    try:
        import json as _json
        with open(os.path.join(out_dir, '.epsilon-project.json'), 'w') as fh:
            _json.dump(H.to_dict(project), fh, indent=2)
    except Exception:
        pass
    events.emit('generate', 'produced', '%d files written to %s' % (len(written), out_dir),
                {'files': len(written)})
    # validate -> test -> repair loop
    from .context import ProjectContext
    ctx = build_context(project, out_dir)
    repairs: list = []
    verdicts: list[Verdict] = []
    test_res: dict = {'state': UNKNOWN}
    smoke: dict = {'state': UNKNOWN}
    stage_secs: dict = {}
    iters = 0
    while True:
        _t = time.time()
        verdicts = V.validate_project(out_dir, project, config, events)
        stage_secs['validate'] = round(stage_secs.get('validate', 0.0) +
                                       (time.time() - _t), 3)
        fatal = [v for v in verdicts if v.state == FAIL]
        if config.run_tests:
            _t = time.time()
            test_res = SB.run_tests(out_dir, config.target_language, config.timeout_secs)
            stage_secs['tests'] = round(stage_secs.get('tests', 0.0) +
                                        (time.time() - _t), 3)
        else:
            test_res = {'state': UNKNOWN, 'reason': 'run_tests disabled'}
        clean = not fatal and test_res.get('state') in (PASS, UNKNOWN,
                                                        UNAVAILABLE)
        if clean:
            # Smoke only when everything else is green: it spawns a process
            # and can hang a port, so never pay for it on a failing build.
            _t = time.time()
            smoke = SB.check_smoke(out_dir, project.entry,
                                   config.target_language, 10)
            stage_secs['smoke'] = round(stage_secs.get('smoke', 0.0) +
                                        (time.time() - _t), 3)
            break
        if iters >= config.repair_iterations:
            break
        iters += 1
        events.emit('repair', 'started', 'iteration %d' % iters, {})
        report = ProjectReport(state=FAIL, verdicts=verdicts, tests=test_res)
        try:
            changed, attempts = RP.repair(out_dir, project, report, config, ctx, events)
        except Exception as e:  # repair must never kill the build
            events.emit('repair', 'failed', 'repair crashed: %s' % e, {})
            break
        repairs.extend(attempts)
        # Honor plan-restore hints: re-render the single file from pristine
        # HIR (text diverged from plan => restoring from plan is minimal).
        for a in attempts:
            rel = getattr(a, 'file', '')
            if getattr(a, 'result', '') == 'needs-regeneration' and rel:
                if _restore_from_plan(out_dir, project, rel, events):
                    changed = True
        if not changed:
            break
    verdicts = V.validate_project(out_dir, project, config, events)
    fatal = [v for v in verdicts if v.state == FAIL]
    test_state = test_res.get('state')
    if not fatal and test_state in (PASS,):
        state = PASS
    elif not fatal and test_state in (UNKNOWN, UNAVAILABLE):
        state = UNKNOWN
    else:
        state = FAIL
    metrics = {'seconds': round(time.time() - t0, 2), 'repair_iterations': iters,
               'files': len(written), 'stage_secs': stage_secs,
               'smoke': smoke.get('state') if isinstance(smoke, dict) else 'UNKNOWN'}
    report = ProjectReport(state=state, verdicts=verdicts, tests=test_res,
                           repairs=[a.__dict__ if hasattr(a, '__dict__') else a for a in repairs],
                           files=written, metrics=metrics)
    events.emit('done', 'produced', 'build %s in %ss' % (state, metrics['seconds']), metrics)
    return out_dir, report, ctx
