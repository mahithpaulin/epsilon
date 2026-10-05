"""Epsilon v2 expression micro-language. Real tokenizer + Pratt parser.

Used when a requirement states a formula/predicate as text (e.g. "a + b",
"n % 2 == 0", "x in vowels"). Produces hir expression nodes. Raises
ExprError on bad input instead of regex-guessing.
"""
from __future__ import annotations

import re


class ExprError(ValueError):
    pass


_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\*\*|//|==|!=|<=|>=|[+\-*/%<>()\[\]{},.:|])
""", re.X)

_PREC = {'or': 1, 'and': 2, 'not': 3,
         '==': 4, '!=': 4, '<': 4, '<=': 4, '>': 4, '>=': 4, 'in': 4, 'not-in': 4,
         '|': 5, '^': 6, '&': 7, '<<': 7.5, '>>': 7.5,
         '+': 8, '-': 8, '*': 9, '/': 9, '//': 9, '%': 9, '**': 10}


def tokenize(text: str) -> list[tuple[str, str]]:
    toks: list[tuple[str, str]] = []
    i, n = 0, len(text)
    while i < n:
        m = _TOKEN.match(text, i)
        if not m:
            raise ExprError('cannot tokenize %r at column %d' % (text, i))
        i = m.end()
        kind = m.lastgroup or ''
        val = m.group()
        if kind == 'ws':
            continue
        toks.append((kind, val))
    return toks


class _P:
    def __init__(self, toks: list[tuple[str, str]]) -> None:
        self.toks = toks
        self.i = 0

    def peek(self) -> tuple[str, str]:
        return self.toks[self.i] if self.i < len(self.toks) else ('eof', '')

    def next(self) -> tuple[str, str]:
        t = self.peek()
        self.i += 1
        return t

    def expect(self, val: str) -> None:
        k, v = self.next()
        if v != val:
            raise ExprError('expected %r, got %r' % (val, v))


def parse_expr(text: str):
    """Parse formula text into hir expression nodes (import is lazy: hir imports this)."""
    from . import hir as H
    toks = tokenize(text)
    if not toks:
        raise ExprError('empty expression')
    p = _P(toks)
    e = _parse_or(p, H)
    if p.i != len(toks):
        raise ExprError('trailing tokens in %r' % text)
    return e


def _parse_or(p: _P, H):
    left = _parse_and(p, H)
    while p.peek()[1] == 'or':
        p.next()
        left = H.BoolOp(op='or', values=[left, _parse_and(p, H)])
    return left


def _parse_and(p: _P, H):
    left = _parse_not(p, H)
    while p.peek()[1] == 'and':
        p.next()
        left = H.BoolOp(op='and', values=[left, _parse_not(p, H)])
    return left


def _parse_not(p: _P, H):
    if p.peek() == ('name', 'not') and _looks_unary_not(p):
        p.next()
        return H.UnaryOp(op='not', operand=_parse_not(p, H))
    return _parse_cmp(p, H)


def _looks_unary_not(p: _P) -> bool:
    # 'not' is unary unless it is the second half of 'not in' — handled in _parse_cmp.
    return True


def _parse_cmp(p: _P, H):
    left = _parse_add(p, H)
    while True:
        k, v = p.peek()
        if v in ('==', '!=', '<', '<=', '>', '>='):
            p.next()
            left = H.Compare(op=v, left=left, right=_parse_add(p, H))
        elif (k, v) == ('name', 'in'):
            p.next()
            left = H.Compare(op='in', left=left, right=_parse_add(p, H))
        elif (k, v) == ('name', 'not') and p.i + 1 < len(p.toks) and p.toks[p.i + 1] == ('name', 'in'):
            p.next()
            p.next()
            left = H.Compare(op='not-in', left=left, right=_parse_add(p, H))
        else:
            return left


def _parse_add(p: _P, H):
    left = _parse_mul(p, H)
    while p.peek()[1] in ('+', '-'):
        op = p.next()[1]
        left = H.BinOp(op=op, left=left, right=_parse_mul(p, H))
    return left


def _parse_mul(p: _P, H):
    left = _parse_pow(p, H)
    while p.peek()[1] in ('*', '/', '//', '%'):
        op = p.next()[1]
        left = H.BinOp(op=op, left=left, right=_parse_pow(p, H))
    return left


def _parse_pow(p: _P, H):
    base = _parse_unary(p, H)
    if p.peek()[1] == '**':
        p.next()
        return H.BinOp(op='**', left=base, right=_parse_pow(p, H))
    return base


def _parse_unary(p: _P, H):
    if p.peek()[1] in ('-', '+', '~'):
        op = p.next()[1]
        return H.UnaryOp(op=op, operand=_parse_unary(p, H))
    return _parse_postfix(p, H)


def _parse_postfix(p: _P, H):
    e = _parse_atom(p, H)
    while True:
        if p.peek()[1] == '.':
            p.next()
            k, v = p.next()
            if k != 'name':
                raise ExprError('expected attribute name, got %r' % v)
            e = H.Attr(obj=e, attr=v)
        elif p.peek()[1] == '(':
            p.next()
            args: list = []
            kwargs: dict = {}
            if p.peek()[1] != ')':
                while True:
                    if p.peek()[0] == 'name' and p.i + 1 < len(p.toks) and p.toks[p.i + 1][1] == '=':
                        nm = p.next()[1]
                        p.next()
                        kwargs[nm] = _parse_or(p, H)
                    else:
                        args.append(_parse_or(p, H))
                    if p.peek()[1] == ',':
                        p.next()
                        continue
                    break
            p.expect(')')
            e = H.Call(func=e, args=args, kwargs=kwargs)
        elif p.peek()[1] == '[':
            p.next()
            idx = _parse_or(p, H)
            p.expect(']')
            e = H.Subscript(obj=e, index=idx)
        else:
            return e


def _parse_atom(p: _P, H):
    k, v = p.next()
    if k == 'num':
        return H.Literal(value=float(v) if '.' in v else int(v))
    if k == 'str':
        q = v[0]
        body = v[1:-1].replace('\\' + q, q).replace('\\\\', '\\')
        return H.Literal(value=body)
    if k == 'name':
        if v in ('True', 'False'):
            return H.Literal(value=(v == 'True'))
        if v == 'None':
            return H.Literal(value=None)
        return H.Var(name=v)
    if v == '(':
        e = _parse_or(p, H)
        p.expect(')')
        return e
    if v == '[':
        items = []
        if p.peek()[1] != ']':
            while True:
                items.append(_parse_or(p, H))
                if p.peek()[1] == ',':
                    p.next()
                    continue
                break
        p.expect(']')
        return H.ListLit(items=items)
    if v == '{':
        pairs = []
        if p.peek()[1] != '}':
            while True:
                key = _parse_or(p, H)
                p.expect(':')
                pairs.append((key, _parse_or(p, H)))
                if p.peek()[1] == ',':
                    p.next()
                    continue
                break
        p.expect('}')
        return H.DictLit(pairs=pairs)
    raise ExprError('unexpected %r' % v)
