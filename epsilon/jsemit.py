"""Epsilon v1 JavaScript emitter — compositional, mirrors Python backend."""

JS_RESERVED_FIX = {'let': 'let_', 'const': 'const_', 'var': 'var_'}


def _nm(n):
    return JS_RESERVED_FIX.get(n, n)


def emit(node, lvl=0, ctx=None):
    ctx = ctx or {'declared': set()}
    k = node['kind']
    if k == 'Module':
        return _module(node, ctx)
    if k == 'FuncDef':
        return _func(node, lvl, ctx)
    if k == 'Return':
        return _ind(lvl) + 'return %s;' % emit_expr(node['value'], ctx) if node.get('value') is not None else _ind(lvl) + 'return;'
    if k == 'Assign':
        return _assign(node, lvl, ctx)
    if k == 'If':
        return _if(node, lvl, ctx)
    if k == 'ForRange':
        return _for(node, lvl, ctx)
    if k == 'While':
        return '%swhile (%s) {\n%s\n%s}' % (_ind(lvl), emit_expr(node['cond'], ctx),
                                            emit_block(node.get('body', []), lvl + 1, ctx), _ind(lvl))
    if k == 'Print':
        return _ind(lvl) + 'console.log(%s);' % ', '.join(emit_expr(v, ctx) for v in node.get('values', []))
    if k == 'ExprStmt':
        return _exprstmt(node, lvl, ctx)
    raise ValueError('unknown stmt %r' % k)


def _ind(lvl):
    return '  ' * lvl


def emit_block(stmts, lvl, ctx):
    if not stmts:
        return _ind(lvl) + '/* pass */'
    return '\n'.join(emit(s, lvl, ctx) for s in stmts)


def _module(n, ctx):
    parts = []
    for f in n.get('functions', []):
        # fresh declared set per function
        parts.append(emit(f, 0, {'declared': set()}))
    for s in n.get('main', []):
        parts.append(emit(s, 0, {'declared': set()}))
    names = [f['name'] for f in n.get('functions', [])]
    if names:
        parts.append('module.exports = { %s };' % ', '.join(names))
    return '\n\n'.join(parts) + '\n' if parts else '\n'


def _func(n, lvl, ctx):
    params = ', '.join(n.get('params', []))
    body = emit_block(n.get('body', []), lvl + 1, ctx)
    demo = ''
    return '%sfunction %s(%s) {\n%s\n%s}' % (_ind(lvl), n['name'], params, body, _ind(lvl))


def _assign(n, lvl, ctx):
    t = _nm(n['target'])
    v = emit_expr(n['value'], ctx)
    if t in ctx['declared']:
        return '%s%s = %s;' % (_ind(lvl), t, v)
    ctx['declared'].add(t)
    return '%slet %s = %s;' % (_ind(lvl), t, v)


def _if(n, lvl, ctx):
    out = '%sif (%s) {\n%s\n%s}' % (_ind(lvl), emit_expr(n['cond'], ctx),
                                    emit_block(n.get('then', []), lvl + 1, ctx), _ind(lvl))
    for e in n.get('elifs', []):
        out += ' else if (%s) {\n%s\n%s}' % (emit_expr(e['cond'], ctx),
                                             emit_block(e.get('body', []), lvl + 1, ctx), _ind(lvl))
    if n.get('else_body'):
        out += ' else {\n%s\n%s}' % (emit_block(n['else_body'], lvl + 1, ctx), _ind(lvl))
    return out


def _for(n, lvl, ctx):
    v = _nm(n['var'])
    s, e = emit_expr(n['start'], ctx), emit_expr(n['stop'], ctx)
    step = n.get('step')
    st = emit_expr(step, ctx) if step is not None else '1'
    ctx['declared'].add(v)
    # Python range(start, stop) is exclusive; our IR stop is already exclusive-adjusted
    # except when step==1 default — treat uniformly as < stop
    body = emit_block(n.get('body', []), lvl + 1, ctx)
    if st.strip() == '1':
        return '%sfor (let %s = %s; %s < %s; %s += 1) {\n%s\n%s}' % (_ind(lvl), v, s, v, e, v, body, _ind(lvl))
    return '%sfor (let %s = %s; %s < %s; %s += (%s)) {\n%s\n%s}' % (_ind(lvl), v, s, v, e, v, st, body, _ind(lvl))


def _exprstmt(n, lvl, ctx):
    ex = n.get('expr', {})
    if ex.get('kind') == 'Call' and ex.get('func') == 'setitem':
        obj, idx, val = ex['args']
        return '%s%s[%s] = %s;' % (_ind(lvl), emit_expr(obj, ctx), emit_expr(idx, ctx), emit_expr(val, ctx))
    return _ind(lvl) + emit_expr(ex, ctx) + ';'


def emit_expr(e, ctx=None):
    ctx = ctx or {'declared': set()}
    k = e['kind']
    if k == 'Var':
        return _nm(e['name'])
    if k == 'Literal':
        return _lit(e)
    if k == 'BinOp':
        return '(%s %s %s)' % (emit_expr(e['left'], ctx), e['op'], emit_expr(e['right'], ctx))
    if k == 'Compare':
        op = e['op']
        if op == '==':
            op = '==='
        elif op == '!=':
            op = '!=='
        elif op == 'in':
            # x in "aeiou" -> "aeiou".includes(x)
            return '(%s.includes(%s))' % (emit_expr(e['right'], ctx), emit_expr(e['left'], ctx))
        return '(%s %s %s)' % (emit_expr(e['left'], ctx), op, emit_expr(e['right'], ctx))
    if k == 'Call':
        return _call(e, ctx)
    if k == 'ListLiteral':
        return '[%s]' % ', '.join(emit_expr(i, ctx) for i in e.get('items', []))
    if k == 'Subscript':
        return '%s[%s]' % (emit_expr(e['obj'], ctx), emit_expr(e['index'], ctx))
    raise ValueError('unknown expr %r' % k)


def _lit(e):
    import json
    t, v = e['dtype'], e['value']
    if t == 'str':
        return json.dumps(v)
    if t == 'bool':
        return 'true' if v else 'false'
    if t == 'none':
        return 'null'
    return repr(v)


def _call(e, ctx):
    f, args = e['func'], e.get('args', [])
    if f == 'append':
        return '%s.push(%s)' % (emit_expr(args[0], ctx), emit_expr(args[1], ctx))
    if f == 'len':
        return '(%s.length)' % emit_expr(args[0], ctx)
    if f == 'str':
        return 'String(%s)' % emit_expr(args[0], ctx)
    if f == 'reverse_str':
        return '(%s.split("").reverse().join(""))' % emit_expr(args[0], ctx)
    return '%s(%s)' % (f, ', '.join(emit_expr(a, ctx) for a in args))


def synthesize(module):
    import copy
    return emit(copy.deepcopy(module), 0, {'declared': set()})
