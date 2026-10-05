"""Epsilon v2 Python backend. Renders HIR to idiomatic typed Python. No HIR knowledge leaks."""
from __future__ import annotations


IND = '    '


def _pad(n: int) -> str:
    return IND * n


class PythonBackend:
    def language_name(self) -> str:
        return 'python'

    def file_extension(self) -> str:
        return '.py'

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
                out[f.path] = render_doc(f)
        return out

    def render_file(self, src) -> str:
        L: list[str] = []
        if getattr(src, 'doc', ''):
            L.append('"""%s"""' % src.doc.replace('"""', "'''"))
            L.append('')
        for imp in _sorted_imports(getattr(src, 'imports', [])):
            L.append(render_import(imp))
        if getattr(src, 'imports', []):
            L.append('')
        decls = getattr(src, 'declarations', [])
        for i, d in enumerate(decls):
            L.append(self.render_decl(d, 0))
            L.append('')
        main = getattr(src, 'main_block', [])
        if main:
            L.append("if __name__ == '__main__':")
            for s in main:
                L.append(self.render_stmt(s, 1))
        text = '\n'.join(L).rstrip('\n') + '\n'
        return text

    # ---- declarations ----
    def render_decl(self, d, lvl: int) -> str:
        kind = type(d).__name__
        if kind == 'FuncDef':
            return self._func(d, lvl)
        if kind == 'ClassDef':
            return self._class(d, lvl)
        if kind == 'VarDecl':
            return self._var(d, lvl)
        if kind == 'DataModel':
            return self._model_as_class(d, lvl)
        raise ValueError('PythonBackend: unknown declaration %r' % kind)

    def _type(self, t) -> str:
        if t is None:
            return 'Any'
        name = t.name
        args = getattr(t, 'args', []) or []
        base = {'int': 'int', 'float': 'float', 'str': 'str', 'bool': 'bool',
                'list': 'list', 'dict': 'dict', 'tuple': 'tuple', 'set': 'set',
                'None': 'None', 'none': 'None', 'Any': 'Any', 'any': 'Any'}.get(name, name)
        if args:
            base += '[%s]' % ', '.join(self._type(a) for a in args)
        if getattr(t, 'optional', False) and base != 'None':
            base = '%s | None' % base
        return base

    def _func(self, f, lvl: int) -> str:
        p = _pad(lvl)
        lines = []
        for dec in getattr(f, 'decorators', []) or []:
            lines.append('%s@%s' % (p, dec))
        kw = 'async def' if getattr(f, 'is_async', False) else 'def'
        parts = []
        for prm in getattr(f, 'params', []) or []:
            s = prm.name
            if getattr(prm, 'type', None) is not None:
                s += ': %s' % self._type(prm.type)
            if getattr(prm, 'default', None) is not None:
                s += ' = %s' % prm.default
            parts.append(s)
        ret = getattr(f, 'returns', None)
        sig = '%s%s %s(%s)' % (p, kw, f.name, ', '.join(parts))
        if ret is not None:
            sig += ' -> %s' % self._type(ret)
        sig += ':'
        lines.append(sig)
        body = getattr(f, 'body', []) or []
        if getattr(f, 'doc', ''):
            lines.append('%s"""%s"""' % (_pad(lvl + 1), f.doc.replace('"""', "'''")))
        if not body:
            lines.append('%spass' % _pad(lvl + 1))
        for s in body:
            lines.append(self.render_stmt(s, lvl + 1))
        return '\n'.join(lines)

    def _class(self, c, lvl: int) -> str:
        p = _pad(lvl)
        lines = []
        for dec in getattr(c, 'decorators', []) or []:
            lines.append('%s@%s' % (p, dec))
        bases = getattr(c, 'bases', []) or []
        lines.append('%sclass %s%s:' % (p, c.name, '(%s)' % ', '.join(bases) if bases else ''))
        if getattr(c, 'doc', ''):
            lines.append('%s"""%s"""' % (_pad(lvl + 1), c.doc.replace('"""', "'''")))
        fields = getattr(c, 'fields', []) or []
        methods = getattr(c, 'methods', []) or []
        if not fields and not methods:
            lines.append('%spass' % _pad(lvl + 1))
        for fld in fields:
            lines.append(self._var(fld, lvl + 1))
        for m in methods:
            lines.append(self._func(m, lvl + 1))
            lines.append('')
        return '\n'.join(lines).rstrip('\n')

    def _model_as_class(self, m, lvl: int) -> str:
        p = _pad(lvl)
        lines = ['%s@dataclass' % p, '%sclass %s:' % (p, m.name)]
        if getattr(m, 'doc', ''):
            lines.append('%s"""%s"""' % (_pad(lvl + 1), m.doc.replace('"""', "'''")))
        fields = getattr(m, 'fields', []) or []
        if not fields:
            lines.append('%spass' % _pad(lvl + 1))
        for fld in fields:
            lines.append(self._var(fld, lvl + 1))
        return '\n'.join(lines)

    def _var(self, v, lvl: int) -> str:
        s = '%s%s' % (_pad(lvl), v.name)
        if getattr(v, 'type', None) is not None:
            s += ': %s' % self._type(v.type)
        if getattr(v, 'value', None) is not None:
            s += ' = %s' % v.value
        return s

    # ---- statements ----
    def render_stmt(self, s, lvl: int) -> str:
        kind = type(s).__name__
        p = _pad(lvl)
        if kind == 'Assign':
            t = '%s%s' % (p, s.target)
            if getattr(s, 'annotation', None) is not None:
                t += ': %s' % self._type(s.annotation)
            return '%s = %s' % (t, self.render_expr(s.value))
        if kind == 'IndexAssign':
            return '%s%s[%s] = %s' % (p, self.render_expr(s.obj), self.render_expr(s.index), self.render_expr(s.value))
        if kind == 'AttrAssign':
            return '%s%s.%s = %s' % (p, self.render_expr(s.obj), s.attr, self.render_expr(s.value))
        if kind == 'AugAssign':
            return '%s%s %s %s' % (p, s.target, s.op, self.render_expr(s.value))
        if kind == 'Return':
            return '%sreturn%s' % (p, '' if s.value is None else ' ' + self.render_expr(s.value))
        if kind == 'If':
            out = ['%sif %s:' % (p, self.render_expr(s.cond))]
            out.extend(self.render_stmt(x, lvl + 1) for x in s.then or [])
            for cond, body in s.elifs or []:
                out.append('%selif %s:' % (p, self.render_expr(cond)))
                out.extend(self.render_stmt(x, lvl + 1) for x in body or [])
            if s.else_body:
                out.append('%selse:' % p)
                out.extend(self.render_stmt(x, lvl + 1) for x in s.else_body)
            if len(out) == 1:
                out.append('%spass' % _pad(lvl + 1))
            return '\n'.join(out)
        if kind == 'ForIn':
            out = ['%sfor %s in %s:' % (p, s.var, self.render_expr(s.iter))]
            out.extend(self.render_stmt(x, lvl + 1) for x in s.body or [])
            if len(out) == 1:
                out.append('%spass' % _pad(lvl + 1))
            return '\n'.join(out)
        if kind == 'While':
            out = ['%swhile %s:' % (p, self.render_expr(s.cond))]
            out.extend(self.render_stmt(x, lvl + 1) for x in s.body or [])
            if len(out) == 1:
                out.append('%spass' % _pad(lvl + 1))
            return '\n'.join(out)
        if kind == 'Try':
            out = ['%stry:' % p]
            out.extend(self.render_stmt(x, lvl + 1) for x in s.body or [])
            for h in s.handlers or []:
                if getattr(h, 'exc', None):
                    head = '%sexcept %s' % (p, h.exc)
                    if getattr(h, 'name', None):
                        head += ' as %s' % h.name
                    out.append(head + ':')
                else:
                    out.append('%sexcept:' % p)
                out.extend(self.render_stmt(x, lvl + 1) for x in h.body or [])
            if s.else_body:
                out.append('%selse:' % p)
                out.extend(self.render_stmt(x, lvl + 1) for x in s.else_body)
            if s.finally_body:
                out.append('%sfinally:' % p)
                out.extend(self.render_stmt(x, lvl + 1) for x in s.finally_body)
            return '\n'.join(out)
        if kind == 'Raise':
            if s.message is None:
                return '%sraise %s' % (p, s.exc)
            return '%sraise %s(%s)' % (p, s.exc, self.render_expr(s.message))
        if kind == 'Assert':
            if s.message is None:
                return '%sassert %s' % (p, self.render_expr(s.cond))
            return '%sassert %s, %s' % (p, self.render_expr(s.cond), self.render_expr(s.message))
        if kind == 'ExprStmt':
            return '%s%s' % (p, self.render_expr(s.expr))
        if kind == 'With':
            items = []
            for ex, nm in s.items or []:
                items.append(self.render_expr(ex) + (' as %s' % nm if nm else ''))
            out = ['%swith %s:' % (p, ', '.join(items))]
            out.extend(self.render_stmt(x, lvl + 1) for x in s.body or [])
            return '\n'.join(out)
        if kind in ('Pass', 'Break', 'Continue'):
            return '%s%s' % (p, kind.lower())
        raise ValueError('PythonBackend: unknown statement %r' % kind)

    # ---- expressions ----
    def render_expr(self, e) -> str:
        kind = type(e).__name__
        if kind == 'Literal':
            v = e.value
            if v is None:
                return 'None'
            if v is True:
                return 'True'
            if v is False:
                return 'False'
            return repr(v)
        if kind == 'Var':
            return e.name
        if kind == 'BinOp':
            return '(%s %s %s)' % (self.render_expr(e.left), e.op, self.render_expr(e.right))
        if kind == 'Compare':
            return '(%s %s %s)' % (self.render_expr(e.left), e.op, self.render_expr(e.right))
        if kind == 'BoolOp':
            return '(%s)' % (' %s ' % e.op).join(self.render_expr(x) for x in e.values)
        if kind == 'UnaryOp':
            if e.op == 'not':
                return '(not %s)' % self.render_expr(e.operand)
            return '(%s%s)' % (e.op, self.render_expr(e.operand))
        if kind == 'Call':
            if (type(e.func).__name__ == 'Var' and e.func.name == 'reversed'
                    and len(e.args) == 1 and not e.kwargs):
                return '(%s[::-1])' % self.render_expr(e.args[0])
            f = self.render_expr(e.func)
            parts = [self.render_expr(a) for a in e.args or []]
            parts.extend('%s=%s' % (k, self.render_expr(v)) for k, v in (e.kwargs or {}).items())
            return '%s(%s)' % (f, ', '.join(parts))
        if kind == 'Attr':
            return '%s.%s' % (self.render_expr(e.obj), e.attr)
        if kind == 'Subscript':
            return '%s[%s]' % (self.render_expr(e.obj), self.render_expr(e.index))
        if kind == 'ListLit':
            return '[%s]' % ', '.join(self.render_expr(x) for x in e.items or [])
        if kind == 'DictLit':
            return '{%s}' % ', '.join('%s: %s' % (self.render_expr(k), self.render_expr(v)) for k, v in e.pairs or [])
        if kind == 'SetLit':
            items = e.items or []
            return 'set()' if not items else '{%s}' % ', '.join(self.render_expr(x) for x in items)
        if kind == 'FStr':
            parts = []
            for part in e.parts or []:
                if isinstance(part, str):
                    parts.append(part.replace('\\', '\\\\').replace('"', '\\"').replace('{', '{{').replace('}', '}}'))
                else:
                    parts.append('{%s}' % self.render_expr(part))
            return 'f"%s"' % ''.join(parts)
        if kind == 'Await_':
            return 'await %s' % self.render_expr(e.value)
        if kind == 'Starred':
            return '*%s' % self.render_expr(e.value)
        raise ValueError('PythonBackend: unknown expression %r' % kind)


def _sorted_imports(imports: list) -> list:
    def key(imp):
        return (getattr(imp, 'module', ''), ','.join(getattr(imp, 'names', []) or []))
    return sorted(imports or [], key=key)


def render_import(imp) -> str:
    names = getattr(imp, 'names', []) or []
    alias = getattr(imp, 'alias', None)
    if not names:
        return 'import %s%s' % (imp.module, ' as %s' % alias if alias else '')
    return 'from %s import %s' % (imp.module, ', '.join(names))


def render_config(cfg) -> str:
    fmt = getattr(cfg, 'format', 'txt')
    data = getattr(cfg, 'data', '')
    if fmt == 'json':
        import json as _json
        return _json.dumps(data, indent=2) + '\n'
    if fmt == 'toml':
        return _to_toml(data)
    if fmt == 'ini':
        return _to_ini(data)
    return (data if isinstance(data, str) else str(data)) + '\n'


def _toml_val(v) -> str:
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"%s"' % v.replace('\\', '\\\\').replace('"', '\\"')
    if isinstance(v, list):
        return '[%s]' % ', '.join(_toml_val(x) for x in v)
    return '"%s"' % str(v)


def _to_toml(data) -> str:
    if not isinstance(data, dict):
        return str(data) + '\n'
    lines: list[str] = []
    for key, val in data.items():
        if isinstance(val, dict):
            lines.append('[%s]' % key)
            for k2, v2 in val.items():
                if isinstance(v2, dict):
                    lines.append('[%s.%s]' % (key, k2))
                    for k3, v3 in v2.items():
                        lines.append('%s = %s' % (k3, _toml_val(v3)))
                else:
                    lines.append('%s = %s' % (k2, _toml_val(v2)))
        else:
            lines.append('%s = %s' % (key, _toml_val(val)))
    return '\n'.join(lines) + '\n'


def _to_ini(data) -> str:
    if not isinstance(data, dict):
        return str(data) + '\n'
    lines = []
    for key, val in data.items():
        if isinstance(val, dict):
            lines.append('[%s]' % key)
            for k2, v2 in val.items():
                lines.append('%s = %s' % (k2, v2))
        else:
            lines.append('%s = %s' % (key, val))
    return '\n'.join(lines) + '\n'


def render_doc(doc) -> str:
    lines = ['# %s' % getattr(doc, 'title', ''), '']
    for head, body in getattr(doc, 'sections', []) or []:
        lines.append('## %s' % head)
        lines.append('')
        lines.append(str(body))
        lines.append('')
    return '\n'.join(lines).rstrip('\n') + '\n'
