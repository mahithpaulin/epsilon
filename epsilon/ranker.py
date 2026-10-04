"""Epsilon v1 tiny neural ranker — single-layer perceptron, 20 feats + bias = 21 params.

Stdlib only (no numpy — no aarch64 wheels on Termux). Optional: engine works
with ranker disabled. Cannot memorize programs: 21 linear params over lossy
aggregate statistics.
"""
import math
import random

N_FEATS = 20
w = [0.0] * N_FEATS
b = 0.0
PARAMS = N_FEATS + 1  # 21
_trained = False


def sigmoid(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


def extract(code):
    c = code or ''
    lines = c.split('\n')
    n = max(1, len(c))
    def has(s):
        return 1.0 if s in c else 0.0
    # 1 has_def 2 has_return 3 name_valid 4 paren_bal 5 quotes_bal
    f1 = has('def ') or has('function ')
    f2 = has('return')
    f3 = 1.0  # validated elsewhere; cheap proxy: no 'def (' malformed
    f3 = 0.0 if 'def (' in c or 'function (' in c else 1.0
    f4 = 1.0 if c.count('(') == c.count(')') else 0.0
    f5 = 1.0 if (c.count('"') % 2 == 0 and c.count("'") % 2 == 0) else 0.0
    # 6 indent_consistent: all leading-space counts multiple of 2 or 4
    f6 = 1.0
    for ln in lines:
        if ln.strip():
            lead = len(ln) - len(ln.lstrip(' '))
            if lead % 2 != 0:
                f6 = 0.0
                break
    # 7 parses
    try:
        compile(c, '<c>', 'exec')
        f7 = 1.0
    except Exception:
        f7 = 0.0
    f8 = 0.0 if any(k in c for k in ('import os', 'eval(', 'exec(', 'open(')) else 1.0
    kw = sum(c.count(k) for k in ('def ', 'return', 'if ', 'for ', 'in '))
    f9 = min(1.0, kw / 10.0)
    f10 = min(1.0, sum(c.count(o) for o in '+-*/%') / 20.0)
    f11 = min(1.0, (sum(len(l) for l in lines) / max(1, len(lines))) / 80.0)
    f12 = min(1.0, len(lines) / 10.0)
    f13 = 1.0 if c.endswith('\n') else 0.0
    f14 = 1.0 if '=' in c else 0.0
    f15 = 1.0 if '(' in c and ')' in c else 0.0
    f16 = min(1.0, c.count('#') / 5.0 + c.count('//') / 5.0)
    f17 = min(1.0, sum(ch.isdigit() for ch in c) / 20.0)
    f18 = 1.0
    for ln in lines:
        s = ln.strip()
        if s.startswith(('def ', 'if ', 'for ', 'while ', 'elif ', 'else')) and not s.endswith(':') and 'function' not in s and '{' not in s:
            f18 = 0.0
            break
    f19 = 1.0 if 20 < len(c) < 2000 else 0.0
    # penalize empty-param def only when present
    f20 = 0.0 if ('()' in c and f1) else 1.0
    return [f1, f2, f3, f4, f5, f6, f7, f8, f9, f10, f11, f12, f13, f14, f15, f16, f17, f18, f19, f20]


def score(feats):
    z = sum(a * x for a, x in zip(w, feats)) + b
    return sigmoid(z)


def rank(candidates):
    scored = []
    for c in candidates:
        try:
            scored.append((score(extract(c)), c))
        except Exception:
            scored.append((0.0, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _, c in scored]


_CORPUS = [
    ('def add(a, b):\n    return a + b\n', 1),
    ('def fact(n):\n    if (n <= 1):\n        return 1\n    return (n * 1)\n', 1),
    ('def f(x):\n    return (x * x)\n', 1),
    ('def (:\n return(((', 0),
    ('def f(\n  return', 0),
    ('xxxx ((( ))) yyyy', 0),
    ('function add(a, b) {\n  return a + b;\n}\n', 1),
    ('function f(x) {\n  return (x * x);\n}\n', 1),
    ('function (((', 0),
    ('var x = ;;;', 0),
]


def train(epochs=200, lr=0.1, seed=0):
    global w, b, _trained
    rng = random.Random(seed)
    data = [(extract(c), y) for c, y in _CORPUS]
    w = [0.0] * N_FEATS
    b = 0.0
    for _ in range(epochs):
        rng.shuffle(data)
        for x, y in data:
            p = sigmoid(sum(a * v for a, v in zip(w, x)) + b)
            err = p - y
            w = [a - lr * err * v for a, v in zip(w, x)]
            b -= lr * err
    _trained = True
    return {'params': PARAMS, 'trained': True}


def ensure_trained():
    if not _trained:
        try:
            train()
        except Exception:
            pass
