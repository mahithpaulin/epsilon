"""Epsilon v2 -> v1 compatibility shim. QUARANTINED legacy adapter.

v1 accepted one-line algorithm specs. v2's native path is plan_project(),
which is fully general. This module maps the 16 legacy single-function
patterns to (name, params, full snippet behaviors) and shares ALL downstream
machinery (snippets, backends, validation) with v2. Anything unmatched gets
the honest generic fallback (syntax ok, may fail exec) — never a pasted
program. Do not extend this table; extend the planner instead.
"""
from __future__ import annotations

import re

from . import hir as H

LEGACY_OPS = [
    ('fizzbuzz', re.compile(r'fizz\s*-?\s*buzz', re.I)),
    ('factorial', re.compile(r'factorial|\bfact\b', re.I)),
    ('fibonacci', re.compile(r'fibonacci|\bfib\b', re.I)),
    ('prime', re.compile(r'prime|is_prime', re.I)),
    ('palindrome', re.compile(r'palindrome', re.I)),
    ('reverse', re.compile(r'revers\w*\s+str|reverse_string', re.I)),
    ('sort', re.compile(r'\bsort\b', re.I)),
    ('max', re.compile(r'\bmax\b.*\blist\b|\bmax\b.*\[', re.I)),
    ('vowels', re.compile(r'vowel', re.I)),
    ('evenodd', re.compile(r'\beven\b|\bodd\b', re.I)),
    ('sumn', re.compile(r'sum_?1_?to_?n|sum\s+1\b.*\bn\b', re.I)),
    ('squares', re.compile(r'square|print.*squares|squares.*print', re.I)),
    ('arith', re.compile(r'\b(add|plus|sum|subtract|minus|multiply|times|product|divide|power|average|mean|mul)\b', re.I)),
]


def _explicit_name(spec: str) -> str | None:
    m = re.search(r'function\s+([A-Za-z_]\w*)', spec)
    if m:
        return _safe_ident(m.group(1))
    return None


def _safe_ident(s: str, default: str = 'do_task') -> str:
    s = re.sub(r'[^0-9A-Za-z_]+', '_', (s or '').strip())
    s = s.strip('_')
    if not s:
        return default
    if s[0].isdigit():
        s = 'f_' + s
    import keyword
    if keyword.iskeyword(s):
        s += '_fn'
    return s  # NOTE: case preserved (v1 lowercased everything; v2 does not)


def _params_in_parens(spec: str) -> list[str] | None:
    m = re.search(r'\(([^)]*)\)', spec)
    if not m:
        return None
    parts = [_safe_ident(p, 'x') for p in re.split(r'[, ]+', m.group(1)) if p.strip()]
    return parts or None


def classify(spec: str) -> str:
    for kind, pat in LEGACY_OPS:
        if pat.search(spec or ''):
            return kind
    return 'generic'


def behaviors_for(kind: str, func: str, params: list[str]) -> list[dict]:
    a = params[0] if len(params) > 0 else 'a'
    b = params[1] if len(params) > 1 else 'b'
    if kind == 'arith':
        return [{'kind': 'compute', 'target': 'result', 'expr': '%s + %s' % (a, b)},
                {'kind': 'return-expr', 'expr': 'result'}]
    if kind == 'factorial':
        return [{'kind': 'guard-raise', 'pred': 'n < 0', 'exc': 'ValueError',
                 'msg': 'negative factorial'},
                {'kind': 'recurse', 'func': func,
                 'base': [{'pred': 'n <= 1', 'value': '1'}],
                 'combine': 'n * %s(n - 1)' % func}]
    if kind == 'fibonacci':
        return [{'kind': 'guard-raise', 'pred': 'n < 0', 'exc': 'ValueError',
                 'msg': 'negative fibonacci'},
                {'kind': 'recurse', 'func': func,
                 'base': [{'pred': 'n <= 1', 'value': 'n'}],
                 'combine': '%s(n - 1) + %s(n - 2)' % (func, func)}]
    if kind == 'prime':
        return [{'kind': 'branch-return',
                 'branches': [{'pred': 'n < 2', 'value': 'False'}]},
                {'kind': 'compute', 'target': 'cands', 'expr': 'range(2, n)'},
                {'kind': 'none-where', 'source': 'cands', 'item': 'i',
                 'pred': 'n % i == 0', 'target': 'ok'},
                {'kind': 'return-expr', 'expr': 'ok'}]
    if kind == 'palindrome':
        return [{'kind': 'reverse-seq', 'source': a, 'target': 'rev'},
                {'kind': 'return-expr', 'expr': '%s == rev' % a}]
    if kind == 'reverse':
        return [{'kind': 'reverse-seq', 'source': a, 'target': 'result'},
                {'kind': 'return-expr', 'expr': 'result'}]
    if kind == 'sort':
        return [{'kind': 'sort-by', 'source': a, 'target': 'result',
                 'key': None, 'reverse': False},
                {'kind': 'return-expr', 'expr': 'result'}]
    if kind == 'max':
        return [{'kind': 'guard-raise', 'pred': 'len(%s) == 0' % a,
                 'exc': 'ValueError', 'msg': 'empty sequence'},
                {'kind': 'minmax-loop', 'source': a, 'item': 'it',
                 'target': 'result', 'mode': 'max'},
                {'kind': 'return-expr', 'expr': 'result'}]
    if kind == 'vowels':
        return [{'kind': 'count-where', 'source': a, 'item': 'c',
                 'pred': 'c in "aeiouAEIOU"', 'target': 'result'},
                {'kind': 'return-expr', 'expr': 'result'}]
    if kind == 'evenodd':
        return [{'kind': 'branch-return', 'branches': [
            {'pred': '%s %% 2 == 0' % a, 'value': "'even'"},
            {'pred': '%s %% 2 != 0' % a, 'value': "'odd'"}]}]
    if kind == 'sumn':
        return [{'kind': 'compute', 'target': 'result',
                 'expr': '%s * (%s + 1) // 2' % (a, a)},
                {'kind': 'return-expr', 'expr': 'result'}]
    if kind == 'squares':
        return [{'kind': 'repeat-range', 'var': 'i', 'from': '1',
                 'to': '%s + 1' % a,
                 'body': [{'kind': 'compute', 'target': 'sq',
                           'expr': 'i * i'}]}]
    # generic fallback: null -> ValueError, identity return. Honest stub.
    guards = [{'kind': 'validate-inputs', 'checks': [
        {'param': p, 'pred': '%s != None' % p, 'exc': 'ValueError',
         'msg': 'null %s' % p}]} for p in params]
    if params:
        guards.append({'kind': 'return-expr', 'expr': params[0]})
    else:
        guards.append({'kind': 'return-expr', 'expr': 'None'})
    return guards


def plan_legacy(spec: str, language: str) -> tuple[H.Project, str, list[str]]:
    """Legacy spec -> single-function HIR project. Returns (project, func, params)."""
    kind = classify(spec)
    name = _explicit_name(spec)
    given = _params_in_parens(spec)
    defaults = {'arith': ['a', 'b'], 'sumn': ['n'], 'factorial': ['n'],
                'fibonacci': ['n'], 'prime': ['n'], 'palindrome': ['s'],
                'reverse': ['s'], 'sort': ['xs'], 'max': ['xs'],
                'vowels': ['s'], 'evenodd': ['n'], 'squares': ['n']}
    if kind == 'arith':
        low = spec.lower()
        if re.search(r'subtract|minus|difference', low):
            op, dflt, nm = '-', ['a', 'b'], 'subtract'
        elif re.search(r'multiply|times|product|\bmul\b', low):
            op, dflt, nm = '*', ['a', 'b'], 'mul'
        elif re.search(r'divide|quotient', low):
            op, dflt, nm = '/', ['a', 'b'], 'divide'
        elif re.search(r'power', low):
            op, dflt, nm = '**', ['a', 'b'], 'power'
        elif re.search(r'square', low):
            op, dflt, nm = 'square', ['x'], 'square'
        elif re.search(r'average|mean', low):
            op, dflt, nm = 'average', ['xs'], 'average'
        else:
            op, dflt, nm = '+', ['a', 'b'], 'add'
        params = given or list(dflt)
        name = name or _safe_ident(nm)
        if op == 'square':
            behaviors = [{'kind': 'compute', 'target': 'result',
                          'expr': '%s * %s' % (params[0], params[0])},
                         {'kind': 'return-expr', 'expr': 'result'}]
        elif op == 'average':
            behaviors = [{'kind': 'average', 'source': params[0], 'target': 'result'},
                         {'kind': 'return-expr', 'expr': 'result'}]
        else:
            a = params[0] if len(params) > 0 else 'a'
            bb = params[1] if len(params) > 1 else 'b'
            behaviors = [{'kind': 'compute', 'target': 'result',
                          'expr': '%s %s %s' % (a, op, bb)},
                         {'kind': 'return-expr', 'expr': 'result'}]
        return _project(name, params, behaviors, language, kind), name, params
    params = given or list(defaults.get(kind, ['x']))
    if kind == 'squares' and given:
        # `function square(x) ...` is the pure square function, not the
        # print-squares demo (which has no parameter list).
        p = params[0]
        behaviors = [{'kind': 'compute', 'target': 'result',
                      'expr': '%s * %s' % (p, p)},
                     {'kind': 'return-expr', 'expr': 'result'}]
        name = name or _safe_ident('square')
        return _project(name, params, behaviors, language, 'arith-square'), name, params
    if kind == 'fizzbuzz':
        name = name or 'fizzbuzz'
        func = _fizzbuzz_func(name, params[0] if params else 'n')
        proj = _project(name, params, [], language, kind)
        proj.files[0].declarations = [func]
        return proj, name, params
    name = name or {'factorial': 'factorial', 'fibonacci': 'fibonacci',
                    'prime': 'is_prime', 'palindrome': 'is_palindrome',
                    'reverse': 'reverse_string', 'sort': 'sort_list',
                    'max': 'max_in_list', 'vowels': 'count_vowels',
                    'evenodd': 'even_or_odd', 'sumn': 'sum_1_to_n',
                    'squares': 'print_squares'}.get(kind, 'do_task')
    name = _safe_ident(name)
    behaviors = behaviors_for(kind, name, params)
    return _project(name, params, behaviors, language, kind), name, params


def _project(name, params, behaviors, language, kind) -> H.Project:
    from .snippets import build_body
    lang = (language or 'python').lower()
    plist = [H.Param(name=p) for p in params]
    stmts = build_body(behaviors, name, params) if behaviors else [H.Pass()]
    func = H.FuncDef(name=name, params=plist, returns=None,
                     doc='Legacy %s task function.' % kind,
                     behaviors=[], body=stmts)
    ext = {'python': '.py', 'javascript': '.js', 'typescript': '.ts'}.get(lang, '.py')
    src = H.SourceFile(path='%s%s' % (name, ext), language=lang,
                       doc='Legacy %s module.' % kind, imports=[],
                       declarations=[func], main_block=[])
    proj = H.Project(name=name, description='Legacy single-function project.',
                     language=lang, files=[src], entry=src.path,
                     meta={'project-type': 'legacy', 'legacy-kind': kind})
    return proj


def _fizzbuzz_func(name: str, nparam: str) -> H.FuncDef:
    i, out = 'i', 'result'
    mod = lambda d: H.Compare(op='==', left=H.BinOp(op='%', left=H.Var(name=i),
                                                   right=H.Literal(value=d)),
                              right=H.Literal(value=0))
    body = [H.If(cond=mod(15), then=[_app(out, 'FizzBuzz')], elifs=[
        (mod(3), [_app(out, 'Fizz')]),
        (mod(5), [_app(out, 'Buzz')])],
        else_body=[_app(out, None, expr=H.Call(func=H.Var(name='str'),
                                               args=[H.Var(name=i)], kwargs={}))])]
    return H.FuncDef(
        name=name, params=[H.Param(name=nparam)], returns=None,
        doc='Legacy fizzbuzz task function.', behaviors=[], body=[
            H.Assign(target=out, value=H.ListLit(items=[])),
            H.ForIn(var=i, iter=H.Call(func=H.Var(name='range'),
                                       args=[H.Literal(value=1),
                                             H.BinOp(op='+', left=H.Var(name=nparam),
                                                     right=H.Literal(value=1))],
                                       kwargs={}), body=body),
            H.Return(value=H.Var(name=out))])


def _app(lst: str, text: str | None, expr=None) -> H.ExprStmt:
    val = H.Literal(value=text) if expr is None else expr
    return H.ExprStmt(expr=H.Call(func=H.Attr(obj=H.Var(name=lst), attr='append'),
                                  args=[val], kwargs={}))


def score_body(stmts: list) -> tuple[float, str]:
    """Honest quality score from real signals. Formula documented, no fake NN.

    score = 0.2 * parses + 0.8 * concrete, where parses is 1.0 when the
    rendered module compiles (checked by the caller via validate) and
    concrete is 1.0 when the body contains no NotImplementedError /
    bad-formula guard raises. Single candidate; candidates_considered = 1.
    """
    bad = 0
    total = 0

    def walk(nodes):
        nonlocal bad, total
        for s in nodes or []:
            total += 1
            if type(s).__name__ == 'Raise' and getattr(s, 'exc', '') in (
                    'NotImplementedError', 'ValueError'):
                msg = getattr(getattr(s, 'message', None), 'value', '') or ''
                if 'unsupported behavior' in str(msg) or 'bad formula' in str(msg) \
                        or 'bad behavior' in str(msg):
                    bad += 1
            for key in ('then', 'else_body', 'body', 'finally_body'):
                walk(getattr(s, key, None) or [])
            for _c, b in getattr(s, 'elifs', None) or []:
                walk(b)
            for h in getattr(s, 'handlers', None) or []:
                walk(getattr(h, 'body', None) or [])

    walk(stmts)
    concrete = 0.0 if total and bad else 1.0
    return round(0.2 * 1.0 + 0.8 * concrete, 3), \
        'score = 0.2*parses + 0.8*concrete (single candidate)'


def generate(spec: str, lang: str = 'python'):
    """v1-shaped generate() on top of v2 machinery."""
    from .snippets import build_body  # noqa: ensure built
    from .backends import get_backend
    from . import validate as V
    if not (spec or '').strip():
        raise ValueError('empty spec')
    language = (lang or 'python').lower()
    if language == 'javascript':
        language = 'javascript'
    project, func, params = plan_legacy(spec, language)
    backend = get_backend(language)
    files = backend.render_project(project)
    code = files[project.entry]
    verdicts = V.validate_file_content(code, language)
    syntax_ok = all(v.state in ('PASS',) for v in verdicts)
    score, formula = score_body(project.files[0].declarations[0].body)
    if not syntax_ok:
        score = round(score * 0.2, 3)
    verdict = {'lang': language, 'syntax_ok': bool(syntax_ok), 'exec_ok': False,
               'exec_state': 'UNKNOWN',
               'errors': [e for v in verdicts for e in
                          ([x.to_dict() if hasattr(x, 'to_dict') else x for x in v.errors])],
               'rank_score': score, 'scoring': formula, 'candidates_considered': 1}
    ir = {'functions': [{'name': func, 'params': list(params)}],
          'meta': {'task': project.meta.get('legacy-kind'), 'v2': True},
          '_project': H.to_dict(project)}
    return {'code': code, 'ir': ir, 'verdict': verdict,
            'meta': ir['meta'], 'rank_score': score,
            'ranker_params': 0}
