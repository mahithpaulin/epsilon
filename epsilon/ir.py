"""Epsilon v1 IR — language-agnostic, JSON-serializable dicts. Stdlib only."""
import re

NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
PY_KEYWORDS = {
    'False', 'None', 'True', 'and', 'as', 'assert', 'async', 'await',
    'break', 'class', 'continue', 'def', 'del', 'elif', 'else', 'except',
    'finally', 'for', 'from', 'global', 'if', 'import', 'in', 'is',
    'lambda', 'nonlocal', 'not', 'or', 'pass', 'raise', 'return',
    'try', 'while', 'with', 'yield',
}
JS_KEYWORDS = {
    'break', 'case', 'catch', 'class', 'const', 'continue', 'debugger',
    'default', 'delete', 'do', 'else', 'export', 'extends', 'false',
    'finally', 'for', 'function', 'if', 'import', 'in', 'instanceof',
    'new', 'null', 'return', 'super', 'switch', 'this', 'throw',
    'true', 'try', 'typeof', 'var', 'void', 'while', 'with', 'let',
    'static', 'yield', 'await', 'enum',
}

VALID_KINDS = {
    'Module', 'FuncDef', 'Assign', 'Return', 'If', 'ForRange', 'While',
    'Print', 'ExprStmt', 'BinOp', 'Compare', 'Call', 'Literal', 'Var',
    'ListLiteral', 'Subscript',
}


def Lit(value):
    if isinstance(value, bool):
        return {'kind': 'Literal', 'dtype': 'bool', 'value': value}
    if isinstance(value, int):
        return {'kind': 'Literal', 'dtype': 'int', 'value': value}
    if isinstance(value, float):
        return {'kind': 'Literal', 'dtype': 'float', 'value': value}
    if isinstance(value, str):
        return {'kind': 'Literal', 'dtype': 'str', 'value': value}
    if value is None:
        return {'kind': 'Literal', 'dtype': 'none', 'value': None}
    raise ValueError('bad literal %r' % (value,))


def V(name):
    return {'kind': 'Var', 'name': name}


def B(op, left, right):
    return {'kind': 'BinOp', 'op': op, 'left': left, 'right': right}


def C(op, left, right):
    return {'kind': 'Compare', 'op': op, 'left': left, 'right': right}


def Call(func, args=None):
    return {'kind': 'Call', 'func': func, 'args': list(args or [])}


def sanitize_name(s, default='do_task'):
    s = re.sub(r'[^0-9a-zA-Z_]+', '_', (s or '').strip().lower())
    s = s.strip('_')
    if not s:
        return default
    if s[0].isdigit():
        s = 'f_' + s
    if s in PY_KEYWORDS or s in JS_KEYWORDS:
        s = s + '_fn'
    return s


def validate_module(m):
    errs = []
    if not isinstance(m, dict) or m.get('kind') != 'Module':
        return ['root must be Module']
    fns = m.get('functions', [])
    seen = set()
    for f in fns:
        if f.get('kind') != 'FuncDef':
            errs.append('non-FuncDef in functions')
            continue
        n = f.get('name', '')
        if not NAME_RE.match(n or ''):
            errs.append('bad func name %r' % n)
        if n in PY_KEYWORDS or n in JS_KEYWORDS:
            errs.append('func name is keyword %r' % n)
        if n in seen:
            errs.append('dup func %r' % n)
        seen.add(n)
        params = f.get('params', [])
        if len(set(params)) != len(params):
            errs.append('dup params in %s' % n)
        for p in params:
            if not NAME_RE.match(p or ''):
                errs.append('bad param %r in %s' % (p, n))
        errs.extend(_validate_stmts(f.get('body', []), set(params) | {n}, 'func ' + n))
    errs.extend(_validate_stmts(m.get('main', []), set(seen), 'main'))
    return errs


def _validate_stmts(stmts, scope, where):
    errs = []
    scope = set(scope)
    for s in stmts:
        k = s.get('kind')
        if k not in VALID_KINDS:
            errs.append('%s: unknown kind %r' % (where, k))
            continue
        if k == 'Assign':
            t = s.get('target', '')
            if not NAME_RE.match(t or ''):
                errs.append('%s: bad assign target %r' % (where, t))
            errs.extend(_validate_expr(s.get('value'), scope, where))
            scope.add(t)
        elif k == 'Return':
            if s.get('value') is not None:
                errs.extend(_validate_expr(s.get('value'), scope, where))
        elif k == 'If':
            errs.extend(_validate_expr(s.get('cond'), scope, where))
            errs.extend(_validate_stmts(s.get('then', []), set(scope), where))
            for e in s.get('elifs', []):
                errs.extend(_validate_expr(e.get('cond'), scope, where))
                errs.extend(_validate_stmts(e.get('body', []), set(scope), where))
            errs.extend(_validate_stmts(s.get('else_body', []), set(scope), where))
        elif k == 'ForRange':
            v = s.get('var', '')
            if not NAME_RE.match(v or ''):
                errs.append('%s: bad loop var %r' % (where, v))
            for f in ('start', 'stop', 'step'):
                if f in s and s[f] is not None:
                    errs.extend(_validate_expr(s[f], scope, where))
            inner = set(scope) | {v}
            errs.extend(_validate_stmts(s.get('body', []), inner, where))
        elif k == 'While':
            errs.extend(_validate_expr(s.get('cond'), scope, where))
            errs.extend(_validate_stmts(s.get('body', []), set(scope), where))
        elif k == 'Print':
            for e in s.get('values', []):
                errs.extend(_validate_expr(e, scope, where))
        elif k == 'ExprStmt':
            errs.extend(_validate_expr(s.get('expr'), scope, where))
    return errs


def _validate_expr(e, scope, where):
    if e is None:
        return []
    k = e.get('kind')
    if k == 'Var':
        return []  # def-before-use is advisory in v1 (builtins/params vary)
    if k == 'Literal':
        return []
    if k == 'BinOp':
        return _validate_expr(e.get('left'), scope, where) + _validate_expr(e.get('right'), scope, where)
    if k == 'Compare':
        return _validate_expr(e.get('left'), scope, where) + _validate_expr(e.get('right'), scope, where)
    if k == 'Call':
        out = []
        for a in e.get('args', []):
            out.extend(_validate_expr(a, scope, where))
        return out
    if k == 'ListLiteral':
        out = []
        for i in e.get('items', []):
            out.extend(_validate_expr(i, scope, where))
        return out
    if k == 'Subscript':
        return _validate_expr(e.get('obj'), scope, where) + _validate_expr(e.get('index'), scope, where)
    return ['%s: unknown expr kind %r' % (where, k)]
