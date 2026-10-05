"""Epsilon 2.5 generic table-driven renderer. One engine, 25 languages.

Semantics: HIR in, surface syntax out, driven entirely by lang_tables.LangSpec
plus the small per-language maps below (imports, raise, builtins, membership,
file shells). Unknown node kinds raise ValueError naming the kind; legacy-tier
languages additionally refuse non-subset nodes (see spec.gaps).
Documented approximations (e.g. C# .Count vs .Length) are syntax-safe and
flagged in LANGUAGES.md — the matrix bench checks render × syntax, never
claims execution semantics where tools are absent.
"""
from __future__ import annotations

import re as _re

IND = '    '


def _pad(n: int) -> str:
    return IND * n


_TOKENS = ('cond', 'name', 'params', 'ret', 'type', 'val', 'args', 'iter',
           'var', 'exc', 'msg', 'module', 'attr', 'expr')


def _sub(template: str, **kw) -> str:
    """Brace-safe substitution: only {token} placeholders expand; every
    other brace (format strings, blocks) passes through untouched."""

    def rep(m):
        key = m.group(1)
        return str(kw[key]) if key in kw else m.group(0)

    return _re.sub(r'\{([A-Za-z_]+)\}', rep, template)


def _is_word(op: str) -> bool:
    return bool(op) and op[0].isalpha()


IMPORT_T = {
    'default': 'import {module}', 'c': '#include <{module}.h>',
    'cpp': '#include <{module}>', 'rust': 'use {module};',
    'perl': 'use {module};', 'ruby': "require '{module}'",
    'lua': "local {module} = require('{module}')", 'r': 'library({module})',
    'haskell': 'import {module}', 'fortran': 'use {module}',
    'cobol': None, 'matlab': None, 'vb': 'Imports {module}',
    'ada': 'with {module};', 'objc': '#import <{module}.h>',
    'go': 'import "{module}"', 'php': 'use {module};',
    'elixir': 'alias {module}', 'dart': "import '{module}.dart';",
    'java': 'import {module};', 'kotlin': 'import {module}',
    'swift': 'import {module}', 'scala': 'import {module}',
    'julia': 'using {module}', 'csharp': 'using {module};',
    'groovy': 'import {module};',
}

RAISE_T = {
    'default': 'raise {exc}({msg})', 'go': 'panic({msg})',
    'rust': 'panic!({msg})', 'java': 'throw new {exc}({msg})',
    'c': 'abort()', 'cpp': 'throw {exc}({msg})',
    'csharp': 'throw new {exc}({msg})', 'ruby': 'raise {exc}, {msg}',
    'php': 'throw new {exc}({msg})', 'swift': 'fatalError({msg})',
    'kotlin': 'throw {exc}({msg})', 'dart': 'throw {exc}({msg})',
    'lua': 'error({msg})', 'perl': 'die {msg}',
    'r': 'stop({msg})', 'julia': 'error({msg})',
    'scala': 'throw new {exc}({msg})', 'haskell': 'error {msg}',
    'fortran': 'error stop {msg}', 'cobol': 'DISPLAY {msg} STOP RUN',
    'ada': 'raise {exc} with {msg}', 'matlab': 'error({msg})',
    'vb': 'Throw New {exc}({msg})', 'objc': '@throw {exc}',
    'groovy': 'throw new {exc}({msg})', 'elixir': 'raise {msg}',
}

LEN_T = {
    'default': 'len({x})', 'rust': '({x}.len())', 'java': '({x}.size())',
    'cpp': '({x}.size())', 'csharp': '({x}.Count)',
    'ruby': '({x}.length)', 'php': '(count({x}))', 'swift': '({x}.count)',
    'kotlin': '({x}.size)', 'dart': '({x}.length)',
    'lua': '(#{x})', 'perl': '(scalar @{x})', 'r': '(length({x}))',
    'julia': '(length({x}))', 'scala': '({x}.size)',
    'haskell': '(length {x})', 'c': '(strlen({x}))',
    'objc': '([{x} count])', 'groovy': '({x}.size())',
    'elixir': '(length({x}))', 'ada': "({x}'Length)",
    'matlab': '(length({x}))', 'vb': '({x}.Count)',
    'fortran': '(len({x}))', 'r_lang': '(length({x}))',
}

PUSH_T = {
    'default': '{x}.append({v})', 'rust': '{x}.push({v})',
    'java': '{x}.add({v})', 'cpp': '{x}.push_back({v})',
    'csharp': '{x}.Add({v})', 'ruby': '{x} << {v}',
    'php': '{x}[] = {v}', 'swift': '{x}.append({v})',
    'kotlin': '{x}.add({v})', 'dart': '{x}.add({v})',
    'lua': 'table.insert({x}, {v})', 'perl': 'push(@{x}, {v})',
    'r': '{x} <- c({x}, {v})', 'julia': 'push!({x}, {v})',
    'scala': '{x} = {x} :+ {v}', 'haskell': '{x} ++ [{v}]',
    'go': '{x} = append({x}, {v})', 'objc': '[{x} addObject:{v}]',
    'groovy': '{x} << {v}', 'elixir': '{x} = {x} ++ [{v}]',
    'ada': '{x} := {x} & {v}', 'matlab': '{x} = [{x}, {v}]',
    'vb': '{x}.Add({v})', 'fortran': '{x} = [{x}, {v}]',
}

SORTED_T = {
    'default': 'sorted({x})', 'go': 'slices.Sorted({x})',
    'rust': '({{x}.clone().sort(), {x}}).1',
    'java': '({x}.stream().sorted().toList())',
    'cpp': '([](auto v){ std::sort(v.begin(), v.end()); return v; })({x})',
    'csharp': '({x}.OrderBy(v => v).ToList())',
    'ruby': '({x}.sort)', 'php': '(sort({x}), {x})[1]',
    'swift': '({x}.sorted())', 'kotlin': '({x}.sorted())',
    'dart': '([...{x}]..sort())', 'lua': '(function(t) table.sort(t) return t end)({x})',
    'perl': '(sort(@{x}))', 'r': '(sort({x}))', 'julia': '(sort({x}))',
    'scala': '({x}.sorted)', 'haskell': '(Data.List.sort {x})',
    'objc': '([{x} sortedArrayUsingSelector:@selector(compare:)])',
    'groovy': '({x}.sort())', 'elixir': '(Enum.sort({x}))',
    'ada': '(Sort({x}))', 'matlab': '(sort({x}))', 'vb': '({x}.OrderBy())',
    'fortran': '(sort({x}))',
}

REVERSED_T = {
    'default': 'list(reversed({x}))', 'rust': '({x}.iter().rev().collect())',
    'java': '(new java.util.ArrayList<>({x}))',
    'cpp': '([](auto v){ std::reverse(v.begin(), v.end()); return v; })({x})',
    'csharp': '({x}.AsEnumerable().Reverse().ToList())',
    'ruby': '({x}.reverse)', 'php': '(array_reverse({x}))',
    'swift': '(Array({x}.reversed()))', 'kotlin': '({x}.reversed())',
    'dart': '({x}.reversed.toList())', 'lua': '(rev({x}))',
    'perl': '(reverse(@{x}))', 'r': '(rev({x}))', 'julia': '(reverse({x}))',
    'scala': '({x}.reverse)', 'haskell': '(reverse {x})',
    'go': '(rev({x}))', 'objc': '([{x} reverseObjectEnumerator])',
    'groovy': '({x}.reverse())', 'elixir': '(Enum.reverse({x}))',
    'ada': '(Rev({x}))', 'matlab': '(flip({x}))', 'vb': '({x}.Reverse())',
    'fortran': '({x}(size({x}):1:-1))',
}

STR_T = {
    'default': 'str({x})', 'rust': '({x}.to_string())',
    'java': '(String.valueOf({x}))', 'cpp': '(std::to_string({x}))',
    'csharp': '({x}.ToString())', 'ruby': '({x}.to_s)',
    'php': '((string)({x}))', 'swift': '(String(describing: {x}))',
    'kotlin': '({x}.toString())', 'dart': '({x}.toString())',
    'lua': '(tostring({x}))', 'perl': '(\"{x}\")',
    'r': '(as.character({x}))', 'julia': '(string({x}))',
    'scala': '({x}.toString)', 'haskell': '(show {x})',
    'go': '(fmt.Sprint({x}))', 'objc': '([NSString stringWithFormat:@\"%@\", {x}])',
    'groovy': '({x}.toString())', 'elixir': '(to_string({x}))',
    'ada': "({x}'Image)", 'matlab': '(string({x}))',
    'vb': '({x}.ToString())', 'fortran': '(str({x}))', 'c': '({x})',
}

SUM_T = {
    'default': 'sum({x})', 'rust': '({x}.iter().sum())',
    'java': '({x}.stream().mapToInt(v->v).sum())',
    'cpp': '(std::accumulate({x}.begin(), {x}.end(), 0))',
    'csharp': '({x}.Sum())', 'ruby': '({x}.sum)',
    'php': '(array_sum({x}))', 'swift': '({x}.reduce(0, +))',
    'kotlin': '({x}.sum())', 'dart': '({x}.fold(0, (a, b) => a + b))',
    'lua': '(usum({x}))', 'perl': '(usum(@{x}))',
    'r': '(sum({x}))', 'julia': '(sum({x}))', 'scala': '({x}.sum)',
    'haskell': '(sum {x})', 'go': '(usum({x}))',
    'objc': '([usum {x}])', 'groovy': '({x}.sum())',
    'elixir': '(Enum.sum({x}))', 'ada': '(Sum({x}))',
    'matlab': '(sum({x}))', 'vb': '({x}.Sum())',
    'fortran': '(sum({x}))',
}

PRINT_T = {}  # filled from spec.print_ at render time

MEMBER_T = {  # `x in coll` — None means unsupported (renderer raises)
    'default': None, 'rust': '({c}.contains(&{x}))',
    'java': '({c}.contains({x}))', 'cpp': None,
    'csharp': '({c}.Contains({x}))', 'ruby': '({c}.include?({x}))',
    'php': '(in_array({x}, {c}))', 'swift': '({c}.contains({x}))',
    'kotlin': '(({x}) in ({c}))', 'dart': '({c}.contains({x}))',
    'lua': None, 'perl': None, 'r': '(({x} %in% {c}))',
    'julia': '(({x} in {c}))', 'scala': '({c}.contains({x}))',
    'haskell': '(({x}) `elem` ({c}))', 'go': None,
    'objc': '([{c} containsObject:{x}])', 'groovy': '({c}.contains({x}))',
    'elixir': '(({x} in {c}))', 'ada': '(({x} in {c}))',
    'matlab': '(ismember({x}, {c}))', 'vb': '({c}.Contains({x}))',
    'fortran': '(any({c} == {x}))',
}

FILE_HEAD = {
    'go': 'package main\n\n', 'rust': '',
    'java': None, 'csharp': None,  # wrapped in class (see renderer)
    'haskell': '', 'fortran': '', 'cobol': None, 'ada': '',
    'matlab': '', 'vb': '', 'php': '<?php\n',
}
FILE_TAIL = {'php': ''}

RET_DEFAULT = {'java': 'void', 'c': 'void', 'cpp': 'void', 'csharp': 'void',
               'dart': 'void', 'go': '', 'rust': '', 'kotlin': 'Unit',
               'scala': 'Unit', 'ada': '', 'fortran': '',
               'haskell': '', 'default': ''}

BREAK_T = {'default': 'break', 'vb': 'Exit For', 'elixir': None,
           'haskell': None}
CONTINUE_T = {'default': 'continue', 'vb': 'Continue For',
              'elixir': None, 'haskell': None}


class TableBackend:
    """Generic renderer parameterized by a LangSpec."""

    def __init__(self, spec) -> None:
        self.s = spec

    def language_name(self) -> str:
        return self.s.name

    def file_extension(self) -> str:
        return self.s.ext

    # ---- project ----
    def render_project(self, project) -> dict:
        out: dict[str, str] = {}
        for f in project.files:
            kind = type(f).__name__
            if kind == 'SourceFile':
                out[f.path] = self.render_file(f)
            elif kind == 'ConfigFile':
                from .gen_python import render_config
                out[f.path] = render_config(f)
            elif kind == 'DocFile':
                from .gen_python import render_doc
                out[f.path] = render_doc(f)
        return out

    def render_file(self, src) -> str:
        s = self.s
        L: list[str] = []
        head = FILE_HEAD.get(s.name, '')
        if head is None:
            pass
        elif head:
            L.append(head.rstrip('\n'))
        if getattr(src, 'doc', ''):
            L.append('%s %s' % (s.comment, src.doc.replace('\n', ' ')))
        for imp in sorted(getattr(src, 'imports', []) or [],
                          key=lambda i: i.module):
            L.append(self._import(imp))
        decls = list(getattr(src, 'declarations', []) or [])
        if s.name in ('java', 'csharp'):
            cls = 'Main' if s.name == 'java' else 'Program'
            L.append('public class %s {' % cls if s.name == 'java'
                      else 'class %s {' % cls)
            for d in decls:
                for ln in self.render_decl(d, 1).split('\n'):
                    L.append(ln)
            for st in getattr(src, 'main_block', []) or []:
                L.append(self.render_stmt(st, 2))
            L.append('}')
        else:
            for d in decls:
                L.append(self.render_decl(d, 0))
                L.append('')
            for st in getattr(src, 'main_block', []) or []:
                L.append(self.render_stmt(st, 0))
        text = '\n'.join(L).rstrip('\n') + '\n'
        if s.name == 'cobol' and 'PROCEDURE DIVISION' not in text:
            text = ('IDENTIFICATION DIVISION.\nPROGRAM-ID. GEN.\n'
                    'PROCEDURE DIVISION.\n' + text)
        return text

    def _import(self, imp) -> str:
        t = IMPORT_T.get(self.s.name, IMPORT_T['default'])
        if t is None:
            return '%s import %s' % (self.s.comment, imp.module)
        names = getattr(imp, 'names', []) or []
        mod = imp.module + ('.' + ','.join(names) if names and self.s.name in (
            'java', 'kotlin', 'scala', 'groovy') else '')
        if names and self.s.name not in ('java', 'kotlin', 'scala', 'groovy'):
            return '%s import %s (%s)' % (self.s.comment, imp.module,
                                          ', '.join(names))
        return t.format(module=mod)

    # ---- declarations ----
    def render_decl(self, d, lvl: int) -> str:
        kind = type(d).__name__
        if kind == 'FuncDef':
            return self._func(d, lvl)
        if kind == 'ClassDef':
            return self._class(d, lvl)
        if kind == 'VarDecl':
            return self._var(d, lvl, top=True)
        if kind == 'DataModel':
            return self._model(d, lvl)
        raise ValueError('%s: unknown declaration %r' % (self.s.name, kind))

    def _type(self, t) -> str:
        if t is None:
            return ''
        return self.s.types.get(t.name, t.name)

    def _sig(self, name: str) -> str:
        return name if not self.s.sigil else self.s.sigil + name

    def _params(self, params) -> str:
        parts = []
        for p in params or []:
            t = self._type(getattr(p, 'type', None))
            if t:
                parts.append(_sub(self.s.param, name=self._sig(p.name),
                                                type=t))
            else:
                parts.append(self._sig(p.name))
        return ', '.join(parts)

    def _ret(self, returns) -> str:
        if returns is not None:
            t = self._type(returns)
            if self.s.name == 'rust':
                return (' -> ' + t) if t else ''
            if self.s.name in ('go',):
                return (' ' + t) if t else ''
            if self.s.name == 'haskell':
                return t
            if self.s.name == 'ada':
                return t
            return ' ' + t if t else ''
        return ' ' + RET_DEFAULT.get(self.s.name,
                                    RET_DEFAULT['default'])

    def _func(self, d, lvl: int) -> str:
        s = self.s
        if s.name == 'haskell':
            return self._haskell_func(d, lvl)
        if s.name == 'cobol':
            return self._cobol_func(d, lvl)
        if s.name == 'fortran':
            return self._fortran_func(d, lvl)
        p = _pad(lvl)
        for dec in getattr(d, 'decorators', []) or []:
            pass
        if s.name == 'matlab' and not (getattr(d, 'returns', None) and
                                       self._type(d.returns)):
            head = 'function %s(%s)' % (d.name, self._params(
                getattr(d, 'params', [])))
            lines = ['%s%s' % (p, head)]
            for st in getattr(d, 'body', []) or []:
                lines.append(self.render_stmt(st, lvl + 1))
            lines.append('%s%s' % (p, s.block_close) if s.block_close else '')
            return '\n'.join(x for x in lines if x.strip() != '')
        head = _sub(s.func, name=d.name, params=self._params(
            getattr(d, 'params', [])), ret=self._ret(
                getattr(d, 'returns', None))).rstrip()
        lines = ['%s%s' % (p, head if head.endswith(('{', 'do', 'then'))
                            else head + ('' if head.endswith(('{', ':', 'do'))
                                           else ' ' + s.block_open).rstrip())]
        body = getattr(d, 'body', []) or []
        if not body:
            lines.append('%s%s pass' % (_pad(lvl + 1), s.comment))
        for st in body:
            lines.append(self.render_stmt(st, lvl + 1))
        lines.append('%s%s' % (p, s.block_close) if s.block_close else '')
        return '\n'.join(x for x in lines if x.strip() != '')

    def _class(self, d, lvl: int) -> str:
        s = self.s
        if s.class_style == 'none':
            raise ValueError('%s: classes unsupported (see gaps)' % s.name)
        p = _pad(lvl)
        if s.class_style == 'struct':
            return self._struct(d, lvl)
        lines = ['%s%s' % (p, self._class_head(d))]
        for fld in getattr(d, 'fields', []) or []:
            lines.append(self._var(fld, lvl + 1))
        for m in getattr(d, 'methods', []) or []:
            lines.append(self._func(m, lvl + 1))
            lines.append('')
        lines.append('%s%s' % (p, s.block_close) if s.block_close else '')
        return '\n'.join(x for x in lines if x.strip() != '')

    def _class_head(self, d) -> str:
        s = self.s
        bases = getattr(d, 'bases', []) or []
        n = self.s.name
        if n == 'ruby':
            return 'class %s%s' % (d.name, ' < %s' % bases[0] if bases else '')
        if n == 'scala':
            return 'class %s%s {' % (d.name,
                                    ' extends %s' % bases[0] if bases else '')
        if n in ('java', 'csharp', 'cpp', 'php', 'swift', 'kotlin', 'dart',
                 'groovy', 'objc', 'vb', 'ada', 'matlab', 'perl', 'r',
                 'julia', 'elixir', 'lua'):
            suffix = s.block_open if s.block_open else ''
            head = 'class %s' % d.name
            if bases:
                head += ' extends %s' % bases[0] if n != 'cpp' else ' : public %s' % bases[0]
            return (head + ' ' + suffix).rstrip()
        return 'class %s %s' % (d.name, s.block_open)

    def _struct(self, d, lvl: int) -> str:
        p = _pad(lvl)
        n = self.s.name
        fields = getattr(d, 'fields', []) or []
        if n in ('go', 'rust', 'c', 'cpp', 'julia'):
            kw = {'go': 'type %s struct {' % d.name,
                  'rust': 'struct %s {' % d.name,
                  'c': 'typedef struct {',
                  'cpp': 'struct %s {' % d.name,
                  'julia': 'struct %s' % d.name}[n]
            lines = ['%s%s' % (p, kw)]
            for fld in fields:
                t = self._type(fld.type) or 'int'
                if n == 'go':
                    lines.append('%s%s %s' % (_pad(lvl + 1), fld.name, t))
                elif n == 'rust':
                    lines.append('%s%s: %s,' % (_pad(lvl + 1), fld.name, t))
                elif n in ('c', 'cpp'):
                    lines.append('%s%s %s;' % (_pad(lvl + 1), t, fld.name))
                else:
                    lines.append('%s%s' % (_pad(lvl + 1), fld.name))
            close = {'go': '}', 'rust': '}', 'c': '} %s;' % d.name,
                     'cpp': '};', 'julia': 'end'}[n]
            lines.append('%s%s' % (p, close))
            for m in getattr(d, 'methods', []) or []:
                lines.append(self._func(m, lvl))
                lines.append('')
            return '\n'.join(x for x in lines if x.strip() != '')
        if n == 'lua':
            lines = ['%s%s = {}' % (p, d.name)]
            for m in getattr(d, 'methods', []) or []:
                lines.append(self._func(m, lvl))
            return '\n'.join(lines)
        # perl/r/elixir/objc/vb/ada/matlab: record-ish comment + methods
        lines = ['%s%s %s record' % (p, self.s.comment, d.name)]
        for m in getattr(d, 'methods', []) or []:
            lines.append(self._func(m, lvl))
            lines.append('')
        return '\n'.join(x for x in lines if x.strip() != '')

    def _model(self, m, lvl: int) -> str:
        p = _pad(lvl)
        n = self.s.name
        fields = getattr(m, 'fields', []) or []
        if self.s.class_style == 'none' and n == 'haskell':
            fs = ' '.join(f.name for f in fields) or 'X'
            return '%sdata %s = %s { %s }' % (p, m.name, m.name, fs)
        if n == 'cobol':
            lines = ['%s01 %s.' % (p, m.name.upper())]
            for fld in fields:
                lines.append('%s   05 %s PIC X(64).' % (p, fld.name.upper()))
            return '\n'.join(lines)
        fake = type('C', (), {'name': m.name, 'bases': [],
                              'doc': getattr(m, 'doc', ''),
                              'fields': fields, 'methods': [],
                              'decorators': []})
        return self._class(fake, lvl)

    # ---- statements ----
    def _decl_var(self, name: str, type_s: str, val: str, lvl: int,
                  const: bool = False) -> str:
        s = self.s
        t = s.const if const and s.const else s.var
        return '%s%s%s' % (_pad(lvl), '', _sub(t,
            name=self._sig(name), type=type_s, val=val))

    def render_stmt(self, st, lvl: int) -> str:
        s = self.s
        kind = type(st).__name__
        p = _pad(lvl)
        if kind == 'Assign':
            rhs = self.render_expr(st.value)
            t = self._type(getattr(st, 'annotation', None))
            if s.name == 'cobol':
                return '%sCOMPUTE %s = %s%s' % (p, st.target, rhs, s.stmt_end)
            if s.name == 'haskell':
                return '%slet %s = %s' % (p, st.target, rhs)
            if s.assign != '=':
                return '%s%s' % (p, _sub(s.var,
                    name=self._sig(st.target), type=t, val=rhs).replace(
                    '=', s.assign, 1) if '=' in s.var else
                    '%s %s %s' % (self._sig(st.target), s.assign, rhs))
            if s.name in ('r',):
                return '%s%s %s %s' % (p, self._sig(st.target), s.assign, rhs)
            decl = _sub(s.var, name=self._sig(st.target), type=t, val=rhs)
            return '%s%s%s' % (p, decl, '' if decl.rstrip().endswith(
                ('{', '}', ';', 'end', ':')) or not s.stmt_end else s.stmt_end
                if False else (s.stmt_end if not decl.rstrip().endswith(';')
                                and s.stmt_end else ''))
        if kind in ('IndexAssign', 'AttrAssign'):
            if s.name in ('haskell', 'cobol', 'fortran', 'elixir'):
                raise ValueError('%s: indexed/attr store unsupported' % s.name)
            if kind == 'IndexAssign':
                return '%s%s[%s] %s %s%s' % (
                    p, self.render_expr(st.obj), self.render_expr(st.index),
                    s.assign, self.render_expr(st.value), s.stmt_end)
            return '%s%s.%s %s %s%s' % (
                p, self.render_expr(st.obj), st.attr, s.assign,
                self.render_expr(st.value), s.stmt_end)
        if kind == 'AugAssign':
            if s.name in ('haskell', 'cobol', 'elixir', 'vb', 'lua', 'r',
                          'matlab', 'fortran', 'ada', 'pascal'):
                return '%s%s %s %s %s %s%s' % (
                    p, self._sig(st.target), s.assign, self._sig(st.target),
                    st.op.rstrip('='), self.render_expr(st.value),
                    s.stmt_end)
            return '%s%s %s %s%s' % (p, self._sig(st.target), st.op,
                                    self.render_expr(st.value), s.stmt_end)
        if kind == 'Return':
            if s.ret == '{expr}':
                return '%s%s' % (p, self.render_expr(st.value))
            if st.value is None:
                return '%s%s' % (p, s.ret.replace('{expr}', '').rstrip())
            return '%s%s' % (p, _sub(s.ret,
                expr=self.render_expr(st.value)))
        if kind == 'If':
            return self._if(st, lvl)
        if kind == 'ForIn':
            return self._for(st, lvl)
        if kind == 'While':
            if not s.while_:
                raise ValueError('%s: while unsupported' % s.name)
            return self._block(_sub(s.while_,
                cond=self.render_expr(st.cond)), st.body, lvl)
        if kind == 'Try':
            if not s.try_:
                raise ValueError('%s: try unsupported' % s.name)
            return self._try(st, lvl)
        if kind == 'Raise':
            return self._raise(st, lvl)
        if kind == 'Assert':
            msg = self.render_expr(st.message) if st.message is not None else '"assert"'
            return self.render_stmt(type(st).__name__ and __import__(
                'epsilon.hir', fromlist=['If']).If(
                cond=__import__('epsilon.hir', fromlist=['UnaryOp']).UnaryOp(
                    op='not', operand=st.cond),
                then=[__import__('epsilon.hir', fromlist=['Raise']).Raise(
                    exc='AssertionError', message=st.message)],
                elifs=[], else_body=[]), lvl)
        if kind == 'ExprStmt':
            e = '%s%s' % (p, self.render_expr(st.expr))
            return e + (s.stmt_end if s.stmt_end and not e.rstrip().endswith(
                (';', '{', '}')) else '')
        if kind == 'With':
            lines = ['%s%s with-block {' % (p, s.comment)]
            for x in st.body or []:
                lines.append(self.render_stmt(x, lvl + 1))
            lines.append('%s%s' % (p, s.block_close if s.block_close else 'end'))
            return '\n'.join(lines)
        if kind == 'Pass':
            return '%s%s pass' % (p, s.comment)
        if kind == 'Break':
            t = BREAK_T.get(s.name, BREAK_T['default'])
            if t is None:
                raise ValueError('%s: break unsupported' % s.name)
            return '%s%s%s' % (p, t, s.stmt_end)
        if kind == 'Continue':
            t = CONTINUE_T.get(s.name, CONTINUE_T['default'])
            if t is None:
                raise ValueError('%s: continue unsupported' % s.name)
            return '%s%s%s' % (p, t, s.stmt_end)
        raise ValueError('%s: unknown statement %r' % (s.name, kind))

    def _block(self, head: str, body: list, lvl: int) -> str:
        s = self.s
        p = _pad(lvl)
        lines = ['%s%s' % (p, head)]
        if not body:
            lines.append('%s%s pass' % (_pad(lvl + 1), s.comment))
        for x in body or []:
            lines.append(self.render_stmt(x, lvl + 1))
        if s.block_close:
            lines.append('%s%s' % (p, s.block_close))
        return '\n'.join(lines)

    def _if(self, st, lvl: int) -> str:
        s = self.s
        p = _pad(lvl)
        lines = ['%s%s' % (p, _sub(s.if_, cond=self.render_expr(st.cond)))]
        for x in st.then or []:
            lines.append(self.render_stmt(x, lvl + 1))
        for cond, body in st.elifs or []:
            if s.elif_:
                lines.append('%s%s' % (p, _sub(s.elif_,
                    cond=self.render_expr(cond))))
            else:  # elixir-style: nested if
                lines.append(self._if(type(st)(cond=cond, then=body,
                                              elifs=[], else_body=[]),
                                      lvl))
                continue
            for x in body or []:
                lines.append(self.render_stmt(x, lvl + 1))
        if st.else_body:
            if s.else_:
                lines.append('%s%s' % (p, s.else_))
            for x in st.else_body:
                lines.append(self.render_stmt(x, lvl + 1))
        if s.block_close:
            lines.append('%s%s' % (p, s.block_close))
        return '\n'.join(lines)

    def _for(self, st, lvl: int) -> str:
        from . import hir as H
        s = self.s
        p = _pad(lvl)
        it = st.iter
        rng = None
        if type(it).__name__ == 'Call' and type(it.func).__name__ == 'Var' \
                and it.func.name == 'range':
            rng = it.args
        style = s.for_style
        if style == 'c':
            if rng is None:
                raise ValueError('%s: c-style for needs range()' % s.name)
            lo = self.render_expr(rng[0]) if len(rng) > 0 else '0'
            hi = self.render_expr(rng[1]) if len(rng) > 1 else lo
            if len(rng) <= 1:
                lo, hi = '0', lo
            stp = self.render_expr(rng[2]) if len(rng) > 2 else '1'
            v = self._sig(st.var)
            decl = {'go': ':=', 'java': 'int ', 'c': 'int ',
                    'cpp': 'int ', 'csharp': 'int ', 'dart': 'var ',
                    'perl': 'my ', 'php': ''}[s.name] if s.name in (
                        'go', 'java', 'c', 'cpp', 'csharp', 'dart', 'perl',
                        'php') else ''
            head = 'for (%s%s = %s; %s < %s; %s += %s) {' % (
                decl, v, lo, v, hi, v, stp)
            if s.name == 'perl':
                head = 'for (my %s = %s; %s < %s; %s += %s) {' % (
                    v, lo, v, hi, v, stp)
            if s.name == 'go':
                head = 'for %s := %s; %s < %s; %s += %s {' % (
                    v, lo, v, hi, v, stp)
            if s.name == 'php':
                head = 'for ($%s = %s; $%s < %s; $%s += %s) {' % (
                    st.var, lo, st.var, hi, st.var, stp)
            return self._block(head, st.body, lvl)
        if style == 'in':
            if rng is not None:
                stepped = self._stepped_range(st.var, rng, lvl, st.body)
                if stepped is not None:
                    return stepped
                it = self._range_iter(rng)
            else:
                it = self.render_expr(st.iter)
            head = _sub(s.for_in, var=self._sig(st.var), iter=it)
            return self._block(head, st.body, lvl)
        if style == 'pascal':
            if rng is None:
                raise ValueError('%s: pascal for needs range()' % s.name)
            lo = self.render_expr(rng[0]) if len(rng) > 0 else '0'
            hi = self.render_expr(rng[1]) if len(rng) > 1 else lo
            if len(rng) <= 1:
                lo, hi = '0', lo
            return self._block('for %s := %s to %s do' % (
                st.var, lo, hi), st.body, lvl)
        if style == 'basic':
            if rng is None:
                raise ValueError('%s: for needs range()' % s.name)
            lo = self.render_expr(rng[0]) if len(rng) > 0 else '0'
            hi = self.render_expr(rng[1]) if len(rng) > 1 else lo
            if len(rng) <= 1:
                lo, hi = '0', lo
            n = s.name
            if n == 'lua':
                head = 'for %s = %s, (%s) - 1 do' % (st.var, lo, hi)
            elif n == 'vb':
                head = 'For %s = %s To (%s) - 1' % (st.var, lo, hi)
            else:
                head = 'for %s = %s to %s' % (st.var, lo, hi)
            lines = ['%s%s' % (p, head)]
            for x in st.body or []:
                lines.append(self.render_stmt(x, lvl + 1))
            lines.append('%s%s' % (p, {'lua': 'end', 'vb': 'Next'}.get(n, 'end')))
            return '\n'.join(lines)
        if style == 'do':
            if rng is None:
                raise ValueError('fortran: do needs range()')
            lo = self.render_expr(rng[0]) if len(rng) > 0 else '0'
            hi = self.render_expr(rng[1]) if len(rng) > 1 else lo
            if len(rng) <= 1:
                lo, hi = '0', lo
            lines = ['%sdo %s = %s, (%s) - 1' % (p, st.var, lo, hi)]
            for x in st.body or []:
                lines.append(self.render_stmt(x, lvl + 1))
            lines.append('%send do' % p)
            return '\n'.join(lines)
        if style == 'perform':
            lines = ['%sPERFORM VARYING %s FROM 0 BY 1 UNTIL %s >= %s' % (
                p, st.var.upper(), st.var.upper(),
                self.render_expr(rng[1]) if rng and len(rng) > 1 else '0')]
            for x in st.body or []:
                lines.append(self.render_stmt(x, lvl + 1))
            lines.append('%sEND-PERFORM.' % p)
            return '\n'.join(lines)
        if style == 'each':
            raise ValueError('%s: each-style for needs an iterator' % s.name)
        raise ValueError('%s: unknown for_style %r' % (s.name, style))

    def _stepped_range(self, var: str, rng: list, lvl: int, body: list):
        """range() with explicit non-1 step: exact lowering, else while-loop.

        Returns None when the plain _range_iter path applies (no step given
        or step is the literal 1). Our range(start, stop[, step]) has an
        exclusive stop, matching Python.
        """
        from . import hir as H
        if len(rng) < 3:
            return None
        step = rng[2]
        if type(step).__name__ == 'Literal' and step.value == 1:
            return None
        n = self.s.name
        lo = self.render_expr(rng[0])
        hi = self.render_expr(rng[1])
        st = self.render_expr(step)
        if n == 'rust' and type(step).__name__ == 'Literal' and step.value == -1:
            it = '(%s..%s).rev()' % (hi, lo)
            return self._block(_sub(self.s.for_in, var=self._sig(var),
                                    iter=it), body, lvl)
        if n == 'julia':
            return self._block(_sub(
                self.s.for_in, var=self._sig(var),
                iter='%s:%s:(%s)-1' % (lo, st, hi)), body, lvl)
        if n == 'r':
            return self._block(_sub(
                self.s.for_in, var=self._sig(var),
                iter='seq(%s, (%s)-1, by=(%s))' % (lo, hi, st)), body, lvl)
        if n == 'swift':
            return self._block(_sub(
                self.s.for_in, var=self._sig(var),
                iter='stride(from: %s, to: %s, by: %s)' % (lo, hi, st)),
                body, lvl)
        if n == 'kotlin' and type(step).__name__ == 'Literal' and step.value == -1:
            return self._block(_sub(
                self.s.for_in, var=self._sig(var),
                iter='%s downTo (%s)+1' % (lo, hi)), body, lvl)
        if type(step).__name__ == 'Literal' and isinstance(step.value,
                                                           (int, float)):
            # Universally-correct while lowering for literal steps.
            op = '<' if step.value > 0 else '>'
            p = _pad(lvl)
            intt = self.s.types.get('int') or 'int'
            lines = [self._decl_var(var, intt, lo, lvl)]
            lines.append('%s%s' % (p, _sub(self.s.while_, cond='(%s %s %s)' % (
                self._sig(var), op, hi))))
            for x in body or []:
                lines.append(self.render_stmt(x, lvl + 1))
            lines.append('%s%s %s (%s) + (%s)%s' % (
                _pad(lvl + 1), self._sig(var), self.s.assign,
                self._sig(var), st, self.s.stmt_end))
            lines.append('%s%s' % (p, self.s.block_close)
                         if self.s.block_close else '%send' % p)
            return '\n'.join(lines)
        raise ValueError('%s: non-literal range step unsupported' % n)

    def _range_iter(self, rng: list) -> str:
        n = self.s.name
        lo = self.render_expr(rng[0]) if len(rng) > 0 else '0'
        hi = self.render_expr(rng[1]) if len(rng) > 1 else lo
        if len(rng) <= 1:
            lo, hi = '0', lo
        table = {
            'rust': '%s..%s' % (lo, hi), 'swift': '%s..<%s' % (lo, hi),
            'kotlin': '%s until %s' % (lo, hi), 'julia': '%s:(%s)-1' % (lo, hi),
            'r': '%s:(%s-1)' % (lo, hi), 'scala': '%s until %s' % (lo, hi),
            'haskell': '[%s..(%s-1)]' % (lo, hi),
            'groovy': '%s..(%s-1)' % (lo, hi), 'elixir': '%s..(%s-1)' % (lo, hi),
            'dart': 'Iterable.generate((%s)-(%s), (i) => (%s)+i)' % (hi, lo, lo),
            'ruby': '(%s...(%s))' % (lo, hi), 'perl': '(%s..(%s-1))' % (lo, hi),
            'lua': '%s, (%s)-1' % (lo, hi), 'ada': '%s .. (%s)-1' % (lo, hi),
            'matlab': '%s:(%s)-1' % (lo, hi), 'objc': '%s; %s < %s' % (lo, lo, hi),
            'php': 'range(%s, (%s)-1)' % (lo, hi),
        }
        return table.get(n, '%s..%s' % (lo, hi))

    def _try(self, st, lvl: int) -> str:
        s = self.s
        p = _pad(lvl)
        lines = ['%s%s' % (p, s.try_)]
        for x in st.body or []:
            lines.append(self.render_stmt(x, lvl + 1))
        for h in st.handlers or []:
            exc = getattr(h, 'exc', None) or 'Exception'
            nm = getattr(h, 'name', None) or 'e'
            n = s.name
            if n in ('java', 'cpp', 'csharp', 'php', 'scala', 'groovy',
                     'dart', 'kotlin', 'objc'):
                lines.append('%s} catch (%s %s) {' % (p, exc, nm))
            elif n in ('ruby',):
                lines.append('%srescue %s => %s' % (p, exc, nm))
            elif n in ('lua',):
                lines.append('%s-- handler %s (pcall)' % (p, exc))
            elif n in ('haskell',):
                lines.append('%s-- handler %s' % (p, exc))
            else:
                lines.append('%sexcept %s as %s:' % (p, exc, nm) if False else
                             '%s} catch (%s) {' % (p, nm))
            for x in h.body or []:
                lines.append(self.render_stmt(x, lvl + 1))
        if s.block_close:
            lines.append('%s%s' % (p, s.block_close))
        return '\n'.join(lines)

    def _raise(self, st, lvl: int) -> str:
        t = RAISE_T.get(self.s.name, RAISE_T['default'])
        msg = self.render_expr(st.message) if st.message is not None else '""'
        out = t.format(exc=st.exc, msg=msg)
        if self.s.stmt_end and not out.rstrip().endswith(
                (';', '{', '}', '.', 'end')):
            out += self.s.stmt_end
        return '%s%s' % (_pad(lvl), out)

    # ---- expressions ----
    def render_expr(self, e) -> str:
        s = self.s
        kind = type(e).__name__
        if kind == 'Literal':
            return self._lit(e.value)
        if kind == 'Var':
            return self._sig(e.name)
        if kind == 'BinOp':
            return '(%s %s %s)' % (self.render_expr(e.left), e.op,
                                   self.render_expr(e.right))
        if kind == 'Compare':
            return self._compare(e)
        if kind == 'BoolOp':
            j = (' %s ' % s.and_) if e.op == 'and' else (' %s ' % s.or_)
            if _is_word(s.and_ if e.op == 'and' else s.or_):
                return '(%s)' % j.join(self.render_expr(x) for x in e.values)
            return '(%s)' % j.join(self.render_expr(x) for x in e.values)
        if kind == 'UnaryOp':
            if e.op == 'not':
                if _is_word(s.not_):
                    return '(%s %s)' % (s.not_, self.render_expr(e.operand))
                return '(%s%s)' % (s.not_, self.render_expr(e.operand))
            return '(%s%s)' % (e.op, self.render_expr(e.operand))
        if kind == 'Call':
            return self._call(e)
        if kind == 'Attr':
            return '%s.%s' % (self.render_expr(e.obj), e.attr)
        if kind == 'Subscript':
            return '%s[%s]' % (self.render_expr(e.obj),
                               self.render_expr(e.index))
        if kind == 'ListLit':
            inner = (', '.join(self.render_expr(x) for x in e.items or []))
            return '%s%s%s' % (s.list_open, inner, s.list_close)
        if kind == 'DictLit':
            if not s.dict_open:
                raise ValueError('%s: dict literal unsupported' % s.name)
            return '%s%s%s' % (s.dict_open, ', '.join(
                '%s: %s' % (self.render_expr(k), self.render_expr(v))
                for k, v in e.pairs or []), s.dict_close)
        if kind == 'SetLit':
            raise ValueError('%s: set literal unsupported' % s.name)
        if kind == 'FStr':
            return self._fstr(e)
        if kind == 'Await_':
            return 'await %s' % self.render_expr(e.value)
        if kind == 'Starred':
            return '*%s' % self.render_expr(e.value)
        raise ValueError('%s: unknown expression %r' % (s.name, kind))

    def _lit(self, v) -> str:
        import json as _json
        s = self.s
        if v is None:
            return s.null
        if v is True:
            return s.true
        if v is False:
            return s.false
        if isinstance(v, str):
            if s.name == 'objc':
                return '@%s' % _json.dumps(v)
            return _json.dumps(v)
        if isinstance(v, float):
            return repr(v)
        if isinstance(v, (list, tuple)):
            return '%s%s%s' % (s.list_open, ', '.join(
                self._lit(x) for x in v), s.list_close)
        return repr(v)

    def _compare(self, e) -> str:
        s = self.s
        l = self.render_expr(e.left)
        r = self.render_expr(e.right)
        op = e.op
        if op == '==':
            t = s.str_eq or (s.eq and '%s %s %s' % ('{l}', s.eq, '{r}'))
            if '{l}' in t:
                return '(%s)' % t.format(l=l, r=r)
            return '(%s %s %s)' % (l, s.eq, r)
        if op == '!=':
            t = s.str_ne or (s.ne and '%s %s %s' % ('{l}', s.ne, '{r}'))
            if '{l}' in t:
                return '(%s)' % t.format(l=l, r=r)
            return '(%s %s %s)' % (l, s.ne, r)
        if op in ('in', 'not-in'):
            t = MEMBER_T.get(s.name, MEMBER_T['default'])
            if t is None:
                raise ValueError('%s: membership test unsupported' % s.name)
            test = t.format(x=l, c=r)
            if op == 'not-in':
                test = '(!(%s))' % test if not _is_word(
                    s.not_) else '(not (%s))' % test
            return '(%s)' % test
        if op in ('is', 'is-not') and s.name in ('java', 'csharp', 'cpp'):
            op = '==' if op == 'is' else '!='
        return '(%s %s %s)' % (l, op, r)

    def _call(self, e) -> str:
        s = self.s
        f, fkind = e.func, type(e.func).__name__
        args = [self.render_expr(a) for a in e.args or []]
        kwargs = ['%s=%s' % (k, self.render_expr(v))
                  for k, v in (e.kwargs or {}).items()]
        if fkind == 'Var':
            name = e.func.name
            if name == 'len' and len(args) == 1 and not kwargs:
                return LEN_T.get(s.name, LEN_T['default']).format(x=args[0])
            if name == 'str' and len(args) == 1 and not kwargs:
                return STR_T.get(s.name, STR_T['default']).format(x=args[0])
            if name == 'sorted' and args:
                return SORTED_T.get(s.name, SORTED_T['default']).format(
                    x=args[0])
            if name == 'reversed' and len(args) == 1 and not kwargs:
                return REVERSED_T.get(s.name, REVERSED_T['default']).format(
                    x=args[0])
            if name == 'sum' and len(args) == 1 and not kwargs:
                return SUM_T.get(s.name, SUM_T['default']).format(x=args[0])
            if name == 'range':
                return self._range_call(args)
            if name == 'print' and not kwargs:
                return _sub(s.print_, args=', '.join(args))
            return '%s(%s)' % (self._sig(name), ', '.join(args + kwargs))
        if fkind == 'Attr' and e.func.attr == 'append' and len(args) == 1 \
                and not kwargs:
            base = self.render_expr(e.func.obj)
            t = PUSH_T.get(s.name, PUSH_T['default'])
            return t.format(x=base, v=args[0])
        if fkind == 'Attr' and e.func.attr == 'add' and len(args) == 1 \
                and not kwargs:
            base = self.render_expr(e.func.obj)
            t = PUSH_T.get(s.name, PUSH_T['default'])
            return t.format(x=base, v=args[0])
        return '%s(%s)' % (self.render_expr(e.func),
                           ', '.join(args + kwargs))

    def _range_call(self, args: list) -> str:
        it = self._range_iter_hack(args)
        return it

    def _range_iter_hack(self, args: list) -> str:
        n = self.s.name
        lo = args[0] if len(args) > 0 else '0'
        hi = args[1] if len(args) > 1 else lo
        if len(args) <= 1:
            lo, hi = '0', lo
        table = {
            'rust': '%s..%s' % (lo, hi), 'go': '%s, %s' % (lo, hi),
            'r': '%s:(%s-1)' % (lo, hi),
        }
        return table.get(n, '%s..%s' % (lo, hi))

    def _fstr(self, e) -> str:
        import json as _json
        parts = []
        for part in e.parts or []:
            if isinstance(part, str):
                parts.append(part)
            else:
                parts.append('{E%s}' % self.render_expr(part))
        if self.s.name in ('go', 'c', 'cobol', 'fortran', 'objc', 'haskell',
                           'lua', 'r', 'matlab', 'perl', 'ada', 'vb'):
            return _json.dumps(''.join(
                p if not p.startswith('{E') else '%s' for p in parts))
        return '"%s"' % ''.join(parts)

    # ---- haskell/cobol/fortran function shapes ----
    def _haskell_func(self, d, lvl: int) -> str:
        p = _pad(lvl)
        params = [pr.name for pr in getattr(d, 'params', []) or []]
        body = getattr(d, 'body', []) or []
        if len(body) == 1 and type(body[0]).__name__ == 'Return':
            rhs = self.render_expr(body[0].value)
        elif all(type(x).__name__ in ('Return', 'If') for x in body) and body:
            rhs = self._haskell_block(body)
        else:
            raise ValueError('haskell: only single-expression bodies '
                             'supported (use recursion, not statements)')
        return '%s%s %s = %s' % (p, d.name, ' '.join(params), rhs)

    def _haskell_block(self, stmts: list) -> str:
        if not stmts:
            return '()'
        first = stmts[0]
        if type(first).__name__ == 'Return':
            return self.render_expr(first.value)
        if type(first).__name__ == 'If':
            t = self._haskell_block(first.then)
            e = self._haskell_block(first.else_body) if first.else_body else '()'
            return '(if %s then %s else %s)' % (self.render_expr(first.cond),
                                                t, e)
        raise ValueError('haskell: statement %s unsupported in expression '
                         'position' % type(first).__name__)

    def _cobol_func(self, d, lvl: int) -> str:
        p = _pad(lvl)
        lines = ['%s%s SECTION.' % (p, d.name.upper())]
        for st in getattr(d, 'body', []) or []:
            lines.append(self.render_stmt(st, lvl + 1))
        lines.append('%sEXIT SECTION.' % p)
        return '\n'.join(lines)

    def _fortran_func(self, d, lvl: int) -> str:
        p = _pad(lvl)
        params = ', '.join(pr.name for pr in getattr(d, 'params', []) or [])
        lines = ['%s%s function %s(%s)' % (p, self._ret(
            getattr(d, 'returns', None)).strip() or 'integer', d.name,
            params)]
        for pr in getattr(d, 'params', []) or []:
            t = self._type(pr.type) or 'integer'
            lines.append('%s%s :: %s' % (_pad(lvl + 1), t, pr.name))
        for st in getattr(d, 'body', []) or []:
            lines.append(self.render_stmt(st, lvl + 1))
        lines.append('%send function %s' % (p, d.name))
        return '\n'.join(lines)
