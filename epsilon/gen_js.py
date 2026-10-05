"""Epsilon v2 JavaScript/TypeScript backend. CommonJS output (require/module.exports).

Covers the same HIR as the Python backend. Range loops lower through a
runtime-safe __range helper (negative steps correct). `sorted` lowers to a
numeric comparator sort; `reversed` handles strings and arrays.
"""
from __future__ import annotations

import json as _json
import re as _re

IND = '  '


def _pad(n: int) -> str:
    return IND * n


_TS_TYPES = {'int': 'number', 'float': 'number', 'str': 'string', 'bool': 'boolean',
             'None': 'null', 'none': 'null', 'Any': 'any', 'any': 'any',
             'dict': 'Record<string, any>', 'list': 'any[]', 'tuple': 'any[]', 'set': 'Set<any>'}


class JsBackend:
    def __init__(self, mode: str = 'js') -> None:
        self.mode = 'ts' if mode in ('ts', 'typescript') else 'js'

    def language_name(self) -> str:
        return 'typescript' if self.mode == 'ts' else 'javascript'

    def file_extension(self) -> str:
        return '.ts' if self.mode == 'ts' else '.js'

    # ---- project ----
    def render_project(self, project) -> dict:
        out: dict[str, str] = {}
        for f in project.files:
            kind = type(f).__name__
            if kind == 'SourceFile':
                out[f.path] = self.render_file(f)
            elif kind == 'ConfigFile':
                out[f.path] = render_config(f)
            elif kind == 'DocFile':
                from .gen_python import render_doc
                out[f.path] = render_doc(f)
        return out

    def render_file(self, src) -> str:
        ctx = {'declared': set(), 'reassigned': set(), 'need_range': False, 'need_set_has': False,
               'dict_vars': set()}
        _scan_reassigned(getattr(src, 'declarations', []) or [], getattr(src, 'main_block', []) or [], ctx)
        for d in getattr(src, 'declarations', []) or []:
            if type(d).__name__ == 'VarDecl' and getattr(getattr(d, 'type', None), 'name', '') == 'dict':
                ctx['dict_vars'].add(d.name)
        L: list[str] = []
        if getattr(src, 'doc', ''):
            L.append('/** %s */' % src.doc.replace('*/', '*\\/'))
        reqs = _render_imports(getattr(src, 'imports', []) or [])
        L.extend(reqs)
        if reqs:
            L.append('')
        for d in getattr(src, 'declarations', []) or []:
            L.append(self.render_decl(d, 0, ctx))
            L.append('')
        for s in getattr(src, 'main_block', []) or []:
            L.append(self.render_stmt(s, 0, ctx))
        exports = _export_names(getattr(src, 'declarations', []) or [])
        if exports:
            L.append('module.exports = { %s };' % ', '.join(exports))
        body = '\n'.join(L).rstrip('\n') + '\n'
        if ctx['need_range']:
            body = _RANGE_HELPER + '\n' + body
        return body

    # ---- declarations ----
    def render_decl(self, d, lvl: int, ctx) -> str:
        kind = type(d).__name__
        if kind == 'FuncDef':
            return self._func(d, lvl, ctx)
        if kind == 'ClassDef':
            return self._class(d, lvl, ctx)
        if kind == 'VarDecl':
            return self._var(d, lvl, ctx, top=True)
        if kind == 'DataModel':
            return self._model_as_class(d, lvl, ctx)
        raise ValueError('JsBackend: unknown declaration %r' % kind)

    def _type(self, t) -> str:
        if t is None:
            return 'any'
        name = t.name
        args = getattr(t, 'args', []) or []
        if name == 'list' and args:
            return '%s[]' % self._type(args[0])
        base = _TS_TYPES.get(name, name)
        if getattr(t, 'optional', False) and base != 'null':
            base = '%s | null' % base
        return base

    def _typed(self, name: str, t) -> str:
        if self.mode == 'ts' and t is not None:
            return '%s: %s' % (name, self._type(t))
        return name

    def _jsdoc(self, f, lvl: int) -> list[str]:
        if self.mode == 'ts':
            return []
        lines = ['/**']
        for prm in getattr(f, 'params', []) or []:
            lines.append(' * @param {*} %s %s' % (prm.name, getattr(prm, 'help', '') or ''))
        ret = getattr(f, 'returns', None)
        if ret is not None:
            lines.append(' * @returns {*}')
        if getattr(f, 'doc', ''):
            lines.append(' * %s' % f.doc.replace('*/', '*\\/'))
        lines.append(' */')
        return [_pad(lvl) + x for x in lines]

    def _func(self, f, lvl: int, ctx) -> str:
        p = _pad(lvl)
        saved_dicts = set(ctx.get('dict_vars', set()))
        local = set(saved_dicts)
        for prm in getattr(f, 'params', []) or []:
            if getattr(getattr(prm, 'type', None), 'name', '') == 'dict':
                local.add(prm.name)
        for st in getattr(f, 'body', []) or []:
            _collect_dict_vars(st, local)
        ctx['dict_vars'] = local
        ctx['dict_vars'] = local
        lines = self._jsdoc(f, lvl)
        kw = 'async function' if getattr(f, 'is_async', False) else 'function'
        parts = []
        for prm in getattr(f, 'params', []) or []:
            s = self._typed(prm.name, getattr(prm, 'type', None))
            if getattr(prm, 'default', None) is not None:
                s += ' = %s' % _js_default(prm.default)
            parts.append(s)
        sig = '%s%s %s(%s)' % (p, kw, f.name, ', '.join(parts))
        if self.mode == 'ts':
            ret = getattr(f, 'returns', None)
            sig += ': %s' % (self._type(ret) if ret is not None else 'any')
        sig += ' {'
        lines.append(sig)
        body = getattr(f, 'body', []) or []
        if not body:
            lines.append('%s// pass' % _pad(lvl + 1))
        for s in body:
            lines.append(self.render_stmt(s, lvl + 1, ctx))
        lines.append('%s}' % p)
        ctx['dict_vars'] = saved_dicts
        return '\n'.join(lines)

    def _class(self, c, lvl: int, ctx) -> str:
        p = _pad(lvl)
        bases = getattr(c, 'bases', []) or []
        lines = ['%sclass %s%s {' % (p, c.name, ' extends %s' % bases[0] if bases else '')]
        fields = getattr(c, 'fields', []) or []
        methods = [m for m in getattr(c, 'methods', []) or [] if m.name != '__init__']
        inits = [m for m in getattr(c, 'methods', []) or [] if m.name == '__init__']
        if fields and not inits:
            fp = ', '.join(f.name for f in fields)
            lines.append('%sconstructor(%s) {' % (_pad(lvl + 1), fp))
            for fld in fields:
                val = fld.value if getattr(fld, 'value', None) is not None else fld.name
                lines.append('%sthis.%s = %s;' % (_pad(lvl + 2), fld.name, val))
            lines.append('%s}' % _pad(lvl + 1))
        for m in inits:
            lines.append(self._ctor_from_init(m, lvl + 1, ctx))
        for m in methods:
            lines.append(self._method(m, lvl + 1, ctx))
        if not fields and not methods and not inits:
            lines.append('%s// pass' % _pad(lvl + 1))
        lines.append('%s}' % p)
        return '\n'.join(lines)

    def _ctor_from_init(self, m, lvl: int, ctx) -> str:
        lines = ['%sconstructor(%s) {' % (_pad(lvl), ', '.join(prm.name for prm in m.params or []))]
        for s in m.body or []:
            lines.append(self.render_stmt(s, lvl + 1, ctx))
        lines.append('%s}' % _pad(lvl))
        return '\n'.join(lines)

    def _method(self, m, lvl: int, ctx) -> str:
        lines = self._jsdoc(m, lvl)
        kw = 'async ' if getattr(m, 'is_async', False) else ''
        lines.append('%s%s%s(%s) {' % (_pad(lvl), kw, m.name,
                                       ', '.join(prm.name for prm in m.params or [])))
        for s in m.body or []:
            lines.append(self.render_stmt(s, lvl + 1, ctx))
        lines.append('%s}' % _pad(lvl))
        return '\n'.join(lines)

    def _model_as_class(self, m, lvl: int, ctx) -> str:
        fields = getattr(m, 'fields', []) or []
        lines = ['%sclass %s {' % (_pad(lvl), m.name)]
        fp = ', '.join(f.name for f in fields)
        lines.append('%sconstructor(%s) {' % (_pad(lvl + 1), fp))
        for fld in fields:
            lines.append('%sthis.%s = %s;' % (_pad(lvl + 2), fld.name, fld.name))
        lines.append('%s}' % _pad(lvl + 1))
        lines.append('%s}' % _pad(lvl))
        return '\n'.join(lines)

    def _var(self, d, lvl: int, ctx, top: bool = False) -> str:
        name = d.name
        rhs = d.value if getattr(d, 'value', None) is not None else 'null'
        if top or name not in ctx['declared']:
            ctx['declared'].add(name)
            decl = 'const' if (top or name not in ctx['reassigned']) else 'let'
            s = '%s%s %s = %s;' % (_pad(lvl), decl, name, rhs)
        else:
            s = '%s%s = %s;' % (_pad(lvl), name, rhs)
        if self.mode == 'ts' and getattr(d, 'type', None) is not None and top:
            s = s.replace(' %s =' % name, ' %s: %s =' % (name, self._type(d.type)), 1)
        return s

    # ---- statements ----
    def render_stmt(self, s, lvl: int, ctx) -> str:
        kind = type(s).__name__
        p = _pad(lvl)
        if kind == 'Assign':
            name = s.target
            rhs = self.render_expr(s.value, ctx)
            if name not in ctx['declared']:
                ctx['declared'].add(name)
                decl = 'const' if name not in ctx['reassigned'] else 'let'
                t = ''
                if self.mode == 'ts' and getattr(s, 'annotation', None) is not None:
                    t = ': %s' % self._type(s.annotation)
                return '%s%s %s%s = %s;' % (p, decl, name, t, rhs)
            return '%s%s = %s;' % (p, name, rhs)
        if kind == 'IndexAssign':
            return '%s%s[%s] = %s;' % (p, self.render_expr(s.obj, ctx),
                                       self.render_expr(s.index, ctx), self.render_expr(s.value, ctx))
        if kind == 'AttrAssign':
            return '%s%s.%s = %s;' % (p, self.render_expr(s.obj, ctx), s.attr, self.render_expr(s.value, ctx))
        if kind == 'AugAssign':
            return '%s%s %s %s;' % (p, s.target, s.op, self.render_expr(s.value, ctx))
        if kind == 'Return':
            return '%sreturn%s;' % (p, '' if s.value is None else ' ' + self.render_expr(s.value, ctx))
        if kind == 'If':
            out = ['%sif (%s) {' % (p, self.render_expr(s.cond, ctx))]
            out.extend(self.render_stmt(x, lvl + 1, ctx) for x in s.then or [])
            out[-1:] = out[-1:] or ['%s// pass' % _pad(lvl + 1)]
            for cond, body in s.elifs or []:
                out.append('%s} else if (%s) {' % (p, self.render_expr(cond, ctx)))
                out.extend(self.render_stmt(x, lvl + 1, ctx) for x in body or [])
            if s.else_body:
                out.append('%s} else {' % p)
                out.extend(self.render_stmt(x, lvl + 1, ctx) for x in s.else_body)
            out.append('%s}' % p)
            return '\n'.join(out)
        if kind == 'ForIn':
            it = s.iter
            if type(it).__name__ == 'Call' and type(it.func).__name__ == 'Var' and it.func.name == 'range':
                return self._for_range(s.var, it.args, lvl, s.body or [], ctx)
            out = ['%sfor (const %s of %s) {' % (p, s.var, self.render_expr(it, ctx))]
            out.extend(self.render_stmt(x, lvl + 1, ctx) for x in s.body or [])
            out.append('%s}' % p)
            return '\n'.join(out)
        if kind == 'While':
            out = ['%swhile (%s) {' % (p, self.render_expr(s.cond, ctx))]
            out.extend(self.render_stmt(x, lvl + 1, ctx) for x in s.body or [])
            out.append('%s}' % p)
            return '\n'.join(out)
        if kind == 'Try':
            out = ['%stry {' % p]
            out.extend(self.render_stmt(x, lvl + 1, ctx) for x in s.body or [])
            for h in s.handlers or []:
                nm = getattr(h, 'name', None) or 'e'
                if getattr(h, 'exc', None):
                    out.append('%s} catch (%s) { // %s' % (p, nm, h.exc))
                else:
                    out.append('%s} catch (%s) {' % (p, nm))
                out.extend(self.render_stmt(x, lvl + 1, ctx) for x in h.body or [])
            out.append('%s}' % p)
            if s.finally_body:
                out.append('%sfinally {' % p)
                out.extend(self.render_stmt(x, lvl + 1, ctx) for x in s.finally_body)
                out.append('%s}' % p)
            return '\n'.join(out)
        if kind == 'Raise':
            msg = self.render_expr(s.message, ctx) if s.message is not None else '""'
            if s.exc in ('ValueError', 'TypeError', 'KeyError', 'IndexError',
                         'NotImplementedError', 'AssertionError', 'Exception'):
                return '%sthrow new Error(%s + ": " + (%s));' % (
                    p, _json.dumps(s.exc), msg)
            return '%sthrow new %s(%s);' % (p, s.exc, msg)
        if kind == 'Assert':
            cond = self.render_expr(s.cond, ctx)
            msg = self.render_expr(s.message, ctx) if s.message is not None else '"assertion failed"'
            return '%sif (!(%s)) { throw new Error(%s); }' % (p, cond, msg)
        if kind == 'ExprStmt':
            return '%s%s;' % (p, self.render_expr(s.expr, ctx))
        if kind == 'With':
            out = ['%s{ // with-context inlined' % p]
            out.extend(self.render_stmt(x, lvl + 1, ctx) for x in s.body or [])
            out.append('%s}' % p)
            return '\n'.join(out)
        if kind in ('Pass',):
            return '%s// pass' % p
        if kind in ('Break', 'Continue'):
            return '%s%s;' % (p, kind.lower())
        raise ValueError('JsBackend: unknown statement %r' % kind)

    def _for_range(self, var: str, args: list, lvl: int, body: list, ctx) -> str:
        p = _pad(lvl)
        ctx['need_range'] = True
        ctx['declared'].add(var)
        if len(args) == 1:
            lo, hi, st = '0', self.render_expr(args[0], ctx), '1'
        elif len(args) == 2:
            lo, hi, st = (self.render_expr(args[0], ctx), self.render_expr(args[1], ctx), '1')
        else:
            lo, hi, st = (self.render_expr(args[0], ctx), self.render_expr(args[1], ctx),
                          self.render_expr(args[2], ctx))
        out = ['%sfor (const %s of __range(%s, %s, %s)) {' % (p, var, lo, hi, st)]
        out.extend(self.render_stmt(x, lvl + 1, ctx) for x in body)
        out.append('%s}' % p)
        return '\n'.join(out)

    # ---- expressions ----
    def render_expr(self, e, ctx) -> str:
        kind = type(e).__name__
        if kind == 'Literal':
            return _js_lit(e.value)
        if kind == 'Var':
            return e.name
        if kind == 'BinOp':
            if e.op == '//':
                return '(Math.trunc((%s) / (%s)))' % (self.render_expr(e.left, ctx),
                                                      self.render_expr(e.right, ctx))
            return '(%s %s %s)' % (self.render_expr(e.left, ctx), e.op, self.render_expr(e.right, ctx))
        if kind == 'Compare':
            return self._compare(e, ctx)
        if kind == 'BoolOp':
            joiner = ' && ' if e.op == 'and' else ' || '
            return '(%s)' % joiner.join(self.render_expr(x, ctx) for x in e.values)
        if kind == 'UnaryOp':
            if e.op == 'not':
                return '(!%s)' % self.render_expr(e.operand, ctx)
            return '(%s%s)' % (e.op, self.render_expr(e.operand, ctx))
        if kind == 'Call':
            return self._call(e, ctx)
        if kind == 'Attr':
            return '%s.%s' % (self.render_expr(e.obj, ctx), e.attr)
        if kind == 'Subscript':
            return '%s[%s]' % (self.render_expr(e.obj, ctx), self.render_expr(e.index, ctx))
        if kind == 'ListLit':
            return '[%s]' % ', '.join(self.render_expr(x, ctx) for x in e.items or [])
        if kind == 'DictLit':
            return '{%s}' % ', '.join('%s: %s' % (_js_key(k, self, ctx), self.render_expr(v, ctx)) for k, v in e.pairs or [])
        if kind == 'SetLit':
            return 'new Set([%s])' % ', '.join(self.render_expr(x, ctx) for x in e.items or [])
        if kind == 'FStr':
            parts = []
            for part in e.parts or []:
                if isinstance(part, str):
                    parts.append(part.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$'))
                else:
                    parts.append('${%s}' % self.render_expr(part, ctx))
            return '`%s`' % ''.join(parts)
        if kind == 'Await_':
            return 'await %s' % self.render_expr(e.value, ctx)
        if kind == 'Starred':
            return '...%s' % self.render_expr(e.value, ctx)
        raise ValueError('JsBackend: unknown expression %r' % kind)

    def _compare(self, e, ctx) -> str:
        op = e.op
        l = self.render_expr(e.left, ctx)
        r = self.render_expr(e.right, ctx)
        if op == '==':
            return '(%s === %s)' % (l, r)
        if op == '!=':
            return '(%s !== %s)' % (l, r)
        if op == 'is':
            return '(%s === %s)' % (l, r)
        if op == 'is-not':
            return '(%s !== %s)' % (l, r)
        if op in ('in', 'not-in'):
            right_kind = type(e.right).__name__
            test: str
            if right_kind == 'SetLit':
                test = '%s.has(%s)' % (r, l)
            elif right_kind == 'Var' and e.right.name in ctx.get('set_vars', set()):
                test = '%s.has(%s)' % (r, l)
            elif right_kind == 'DictLit' or (
                    right_kind == 'Var'
                    and e.right.name in ctx.get('dict_vars', set())):
                test = '(%s in %s)' % (l, r)
            else:
                test = '%s.includes(%s)' % (r, l)
            if op == 'not-in':
                test = '(!%s)' % test
            return '(%s)' % test
        return '(%s %s %s)' % (l, op, r)

    def _call(self, e, ctx) -> str:
        f, fkind = e.func, type(e.func).__name__
        args = [self.render_expr(a, ctx) for a in e.args or []]
        kwargs = ['%s: %s' % (k, self.render_expr(v, ctx)) for k, v in (e.kwargs or {}).items()]
        if fkind == 'Var':
            name = e.func.name
            if name == 'len' and len(args) == 1 and not kwargs:
                return '((%s.length !== undefined) ? (%s.length) : (Object.keys(%s).length))' % (args[0], args[0], args[0])
            if name == 'list' and len(args) == 1 and not kwargs:
                return 'Array.from(%s)' % args[0]
            if name == 'reversed' and len(args) == 1 and not kwargs:
                x = args[0]
                return ('((typeof %(x)s === "string") ? %(x)s.split("").reverse().join("")'
                        ' : [...%(x)s].reverse())') % {'x': x}
            if name == 'sorted' and args:
                src = args[0]
                key = dict(e.kwargs or {}).get('key')
                rev = dict(e.kwargs or {}).get('reverse')
                rev_flag = (type(rev).__name__ == 'Literal' and bool(rev.value))
                if key is not None:
                    free = _free_vars(key) - {'True', 'False', 'None'}
                    param = sorted(free)[0] if len(free) == 1 else 'x'
                    kf = self.render_expr(key, ctx)
                    cmp = '((%s) < (%s) ? -1 : ((%s) > (%s) ? 1 : 0))' % (kf, kf.replace(param, '__b', 1), kf, kf.replace(param, '__b', 1))
                    # simpler robust form: comparator on mapped keys
                    cmp = '(function(a, b) { const __ka = (function(%s) { return (%s); })(a); const __kb = (function(%s) { return (%s); })(b); return __ka < __kb ? -1 : (__ka > __kb ? 1 : 0); })' % (param, kf, param, kf)
                    s = '([...%s].sort(%s))' % (src, cmp)
                else:
                    s = '([...%s].sort((a, b) => a - b))' % src
                if rev_flag:
                    s = '(%s.reverse())' % s
                return s
            if name == 'sum' and len(args) == 1 and not kwargs:
                return '(%s.reduce((a, b) => a + b, 0))' % args[0]
            if name == 'str' and len(args) == 1 and not kwargs:
                return 'String(%s)' % args[0]
            if name == 'range':
                ctx['need_range'] = True
                return '__range(%s)' % ', '.join(args)
            if name == 'print' and not kwargs:
                return 'console.log(%s)' % ', '.join(args)
            if name in ('ValueError', 'TypeError', 'KeyError', 'IndexError', 'NotImplementedError'):
                inner = args[0] if args else '""'
                return 'new Error(%s + ": " + (%s))' % (_json.dumps(name), inner)
            all_args = args + kwargs
            return '%s(%s)' % (name, ', '.join(all_args))
        if fkind == 'Attr' and e.func.attr == 'append' and len(args) == 1 and not kwargs:
            return '%s.push(%s)' % (self.render_expr(e.func.obj, ctx), args[0])
        if fkind == 'Attr' and e.func.attr == 'add' and len(args) == 1 and not kwargs:
            return '%s.add(%s)' % (self.render_expr(e.func.obj, ctx), args[0])
        if fkind == 'Attr' and e.func.attr == 'pop' and len(args) == 2 and not kwargs:
            obj = self.render_expr(e.func.obj, ctx)
            return '((%s) in (%s) ? (delete (%s)[%s], true) : null)' % (
                args[0], obj, obj, args[0])
        if fkind == 'Attr' and e.func.attr == 'values' and not args and not kwargs:
            return 'Object.values(%s)' % self.render_expr(e.func.obj, ctx)
        base = self.render_expr(e.func, ctx)
        return '%s(%s)' % (base, ', '.join(args + kwargs))


_RANGE_HELPER = """function __range(start, stop, step) {
  if (stop === undefined) { stop = start; start = 0; }
  if (step === undefined || step === 0) { step = 1; }
  const out = [];
  if (step > 0) { for (let i = start; i < stop; i += step) { out.push(i); } }
  else { for (let i = start; i > stop; i += step) { out.push(i); } }
  return out;
}"""


def _js_lit(v) -> str:
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, str):
        return _json.dumps(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return '[%s]' % ', '.join(_js_lit(x) for x in v)
    if isinstance(v, dict):
        return '{%s}' % ', '.join('%s: %s' % (_js_key_str(k), _js_lit(x)) for k, x in v.items())
    if isinstance(v, set):
        return 'new Set([%s])' % ', '.join(_js_lit(x) for x in sorted(v, key=repr))
    return repr(v)


def _js_key_str(k) -> str:
    if isinstance(k, str) and _re.match(r'^[A-Za-z_$][A-Za-z0-9_$]*$', k):
        return k
    return '[%s]' % _js_lit(k)


def _js_key(k, backend, ctx) -> str:
    if type(k).__name__ == 'Literal' and isinstance(k.value, str) and _re.match(r'^[A-Za-z_$][A-Za-z0-9_$]*$', k.value):
        return k.value
    return '[%s]' % backend.render_expr(k, ctx)


def _js_default(src: str) -> str:
    s = (src or '').strip()
    return {'None': 'null', 'True': 'true', 'False': 'false'}.get(s, s)


def _free_vars(e) -> set:
    kind = type(e).__name__
    if kind == 'Var':
        return {e.name}
    if kind in ('BinOp', 'Compare'):
        return _free_vars(e.left) | _free_vars(e.right)
    if kind == 'BoolOp':
        out: set = set()
        for x in e.values or []:
            out |= _free_vars(x)
        return out
    if kind == 'UnaryOp':
        return _free_vars(e.operand)
    if kind == 'Call':
        out = _free_vars(e.func)
        for a in e.args or []:
            out |= _free_vars(a)
        for v in (e.kwargs or {}).values():
            out |= _free_vars(v)
        return out
    if kind == 'Attr':
        return _free_vars(e.obj)
    if kind == 'Subscript':
        return _free_vars(e.obj) | _free_vars(e.index)
    if kind in ('ListLit', 'SetLit'):
        out = set()
        for x in e.items or []:
            out |= _free_vars(x)
        return out
    if kind == 'DictLit':
        out = set()
        for k, v in e.pairs or []:
            out |= _free_vars(k) | _free_vars(v)
        return out
    return set()


def _collect_dict_vars(st, local: set) -> None:
    """Record dict-typed locals, recursing into nested blocks (if/loop
    branches assign too — e.g. store resolution idioms)."""
    kind = type(st).__name__
    if kind == 'Assign':
        v = getattr(st, 'value', None)
        if type(v).__name__ == 'DictLit':
            local.add(st.target)
        elif type(v).__name__ == 'Var' and v.name in local:
            local.add(st.target)  # dict alias: store = _STORE / records
        return
    for key in ('then', 'else_body', 'body', 'finally_body'):
        for x in getattr(st, key, []) or []:
            _collect_dict_vars(x, local)
    for _c, b in getattr(st, 'elifs', []) or []:
        for x in b or []:
            _collect_dict_vars(x, local)
    for h in getattr(st, 'handlers', []) or []:
        for x in getattr(h, 'body', []) or []:
            _collect_dict_vars(x, local)


def _scan_reassigned(decls: list, main: list, ctx) -> None:
    counts: dict[str, int] = {}

    def stmt(s) -> None:
        k = type(s).__name__
        if k == 'Assign':
            counts[s.target] = counts.get(s.target, 0) + 1
        elif k == 'AugAssign':
            counts[s.target] = counts.get(s.target, 0) + 2
        for key in ('then', 'else_body', 'body', 'finally_body', 'main_block'):
            for x in getattr(s, key, []) or []:
                stmt(x)
        for _, b in getattr(s, 'elifs', []) or []:
            for x in b or []:
                stmt(x)
        for h in getattr(s, 'handlers', []) or []:
            for x in getattr(h, 'body', []) or []:
                stmt(x)

    def decl(d) -> None:
        for s in getattr(d, 'body', []) or []:
            stmt(s)
        for m in getattr(d, 'methods', []) or []:
            for s in getattr(m, 'body', []) or []:
                stmt(s)

    for d in decls:
        decl(d)
    for s in main:
        stmt(s)
    ctx['reassigned'] = {k for k, v in counts.items() if v > 1}


def _render_imports(imports: list) -> list[str]:
    lines = []
    for imp in imports or []:
        mod = getattr(imp, 'module', '')
        names = getattr(imp, 'names', []) or []
        if not names:
            lines.append("const %s = require('%s');" % (getattr(imp, 'alias', None) or mod, mod))
        elif len(names) == 1:
            lines.append("const %s = require('%s').%s;" % (names[0], mod, names[0]))
        else:
            lines.append("const { %s } = require('%s');" % (', '.join(names), mod))
    return lines


def _export_names(decls: list) -> list[str]:
    return [d.name for d in decls or [] if type(d).__name__ in ('FuncDef', 'ClassDef')]


def render_config(cfg) -> str:
    fmt = getattr(cfg, 'format', 'txt')
    data = getattr(cfg, 'data', '')
    if fmt == 'json':
        return _json.dumps(data, indent=2) + '\n'
    return (data if isinstance(data, str) else _json.dumps(data, indent=2)) + '\n'
