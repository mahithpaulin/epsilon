"""Epsilon v1 Python emitter — compositional per-node string templates. No pasted programs."""
import ast


def emit(node, lvl=0):
    k = node['kind']
    fn = _EMIT[k]
    return fn(node, lvl)


def emit_block(stmts, lvl):
    if not stmts:
        return '    ' * lvl + 'pass'
    return '\n'.join(emit(s, lvl) for s in stmts)


def _pad(lvl):
    return '    ' * lvl


def _module(n, lvl):
    parts = []
    for f in n.get('functions', []):
        parts.append(emit(f, 0))
    for s in n.get('main', []):
        parts.append(emit(s, 0))
    return '\n\n'.join(parts) + '\n' if parts else '\n'


def _func(n, lvl):
    p = _pad(lvl)
    params = ', '.join(n.get('params', []))
    return '%sdef %s(%s):\n%s' % (p, n['name'], params, emit_block(n.get('body', []), lvl + 1))


def _ret(n, lvl):
    v = n.get('value')
    if v is None:
        return _pad(lvl) + 'return'
    return _pad(lvl) + 'return ' + emit_expr(v)


def _assign(n, lvl):
    return _pad(lvl) + '%s = %s' % (n['target'], emit_expr(n['value']))


def _if(n, lvl):
    p = _pad(lvl)
    out = '%sif %s:\n%s' % (p, emit_expr(n['cond']), emit_block(n.get('then', []), lvl + 1))
    for e in n.get('elifs', []):
        out += '\n%selif %s:\n%s' % (p, emit_expr(e['cond']), emit_block(e.get('body', []), lvl + 1))
    if n.get('else_body'):
        out += '\n%selse:\n%s' % (p, emit_block(n['else_body'], lvl + 1))
    return out


def _for(n, lvl):
    p = _pad(lvl)
    start, stop, step = n['start'], n['stop'], n.get('step')
    s, e = emit_expr(start), emit_expr(stop)
    body = emit_block(n.get('body', []), lvl + 1)
    if step is not None and not (step.get('kind') == 'Literal' and step.get('value') == 1):
        return '%sfor %s in range(%s, %s, %s):\n%s' % (p, n['var'], s, e, emit_expr(step), body)
    if start.get('kind') == 'Literal' and start.get('value') == 0:
        return '%sfor %s in range(%s):\n%s' % (p, n['var'], e, body)
    return '%sfor %s in range(%s, %s):\n%s' % (p, n['var'], s, e, body)


def _while(n, lvl):
    return '%swhile %s:\n%s' % (_pad(lvl), emit_expr(n['cond']), emit_block(n.get('body', []), lvl + 1))


def _print(n, lvl):
    return _pad(lvl) + 'print(%s)' % ', '.join(emit_expr(v) for v in n.get('values', []))


def _exprstmt(n, lvl):
    ex = n.get('expr', {})
    if ex.get('kind') == 'Call' and ex.get('func') == 'setitem':
        obj, idx, val = ex['args']
        return _pad(lvl) + '%s[%s] = %s' % (emit_expr(obj), emit_expr(idx), emit_expr(val))
    return _pad(lvl) + emit_expr(n['expr'])


def emit_expr(e):
    k = e['kind']
    if k == 'Var':
        return e['name']
    if k == 'Literal':
        return _lit(e)
    if k == 'BinOp':
        return '(%s %s %s)' % (emit_expr(e['left']), e['op'], emit_expr(e['right']))
    if k == 'Compare':
        op = e['op']
        if op == 'in':
            return '(%s in %s)' % (emit_expr(e['left']), emit_expr(e['right']))
        return '(%s %s %s)' % (emit_expr(e['left']), op, emit_expr(e['right']))
    if k == 'Call':
        return _call(e)
    if k == 'ListLiteral':
        return '[%s]' % ', '.join(emit_expr(i) for i in e.get('items', []))
    if k == 'Subscript':
        return '%s[%s]' % (emit_expr(e['obj']), emit_expr(e['index']))
    raise ValueError('unknown expr %r' % k)


def _lit(e):
    t, v = e['dtype'], e['value']
    if t == 'str':
        return repr(v)
    if t == 'bool':
        return 'True' if v else 'False'
    if t == 'none':
        return 'None'
    return repr(v)


def _call(e):
    f, args = e['func'], e.get('args', [])
    if f == 'append':
        # append(lst, val) -> lst.append(val)
        return '%s.append(%s)' % (emit_expr(args[0]), emit_expr(args[1]))
    if f == 'setitem':
        # lowered by caller into indexed store; ExprStmt handles specially
        return '__setitem__(%s)' % ', '.join(emit_expr(a) for a in args)
    if f == 'len':
        return 'len(%s)' % emit_expr(args[0])
    if f == 'str':
        return 'str(%s)' % emit_expr(args[0])
    if f == 'reverse_str':
        # s[::-1]
        return '(%s[::-1])' % emit_expr(args[0])
    return '%s(%s)' % (f, ', '.join(emit_expr(a) for a in args))


_EMIT = {
    'Module': _module,
    'FuncDef': _func,
    'Return': _ret,
    'Assign': _assign,
    'If': _if,
    'ForRange': _for,
    'While': _while,
    'Print': _print,
    'ExprStmt': _exprstmt,
}


def synthesize(module):
    import copy
    module = copy.deepcopy(module)
    module.pop('_has_setitem', None)
    src = emit(module, 0)
    # syntax gate — raise, never silently patch
    ast.parse(src)
    return src


def _lower_setitem(mod):
    import copy
    return copy.deepcopy(mod)
