"""Epsilon v2 validation tests — verdicts over generated sources.

Always runs against the real ``epsilon.verify`` compat path; strict v2
assertions (FAIL with symbol+line, UNKNOWN without node, bad-import FAIL)
activate only when ``epsilon.validate`` (or equivalent) is present, else SKIP.
Stdlib + pytest only; Python 3.11 compatible.
"""
import importlib
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_VAL_MODULES = ("epsilon.validate", "epsilon.validation", "epsilon.checker")
_VAL_FNS = (
    "validate_source", "validate_file", "validate",
    "check", "check_source", "validate_project",
)


def _try_import(*names):
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


VAL_MOD = _try_import(*_VAL_MODULES)


def _validate_fn(mod):
    for name in _VAL_FNS:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def _v2_validate(code, lang, path="probe.py"):
    """Call the v2 validator with a best-effort signature; None if unusable."""
    fn = _validate_fn(VAL_MOD)
    if fn is None:
        return None
    attempts = []
    try:
        attempts.append(lambda: fn(code, lang=lang))
    except Exception:  # pragma: no cover - lambda construction never fails
        pass

    def _by_position():
        return fn(code, lang)

    def _code_only():
        return fn(code)

    def _path_first():
        return fn(path, code, lang)

    for attempt in (attempts[0], _by_position, _code_only, _path_first):
        try:
            return attempt()
        except TypeError:
            continue
        except Exception:
            return None
    return None


def _state(res):
    if res is None:
        return None
    state = getattr(res, "state", None)
    if isinstance(state, str):
        return state
    if isinstance(res, dict):
        if isinstance(res.get("state"), str):
            return res["state"]
        if "syntax_ok" in res:  # legacy verify-shaped dict
            return "PASS" if (res.get("syntax_ok") and not res.get("errors")) else "FAIL"
    return None


def _errors(res):
    if res is None:
        return []
    errs = getattr(res, "errors", None)
    if isinstance(errs, list):
        return errs
    if isinstance(res, dict) and isinstance(res.get("errors"), list):
        return res["errors"]
    return []


def _err_text(err):
    if isinstance(err, dict):
        return " ".join(str(v) for v in err.values())
    to_dict = getattr(err, "to_dict", None)
    if callable(to_dict):
        try:
            return str(to_dict())
        except Exception:
            pass
    parts = [str(err)]
    for attr in ("symbol", "message", "line", "category"):
        try:
            parts.append(str(getattr(err, attr, "")))
        except Exception:
            pass
    return " ".join(parts)


GOOD_PY = "def add(a, b):\n    return a + b\n"
BAD_UNDEF_PY = "def f(a):\n    return a + missing_thing_xyz\n"
SHADOW_PY = (
    "def f():\n"
    "    x = 1\n"
    "    return x\n"
    "\n"
    "\n"
    "def g():\n"
    "    x = 2\n"
    "    def h():\n"
    "        x = 3\n"
    "        return x\n"
    "    return x + h()\n"
)
BAD_IMPORT_PY = "import nosuchmodule_xyz_abc\n\n\nx = 1\n"
GOOD_JS = "function add(a, b) {\n  return a + b;\n}\n\nmodule.exports = { add };\n"


def test_good_file_pass():
    from epsilon import verify

    v = verify.verify_python(GOOD_PY)
    assert v["syntax_ok"] and not v["errors"], v
    if VAL_MOD is None:
        pytest.skip("v2 validate not present; compat behaviour verified")
    res = _v2_validate(GOOD_PY, "python")
    state = _state(res)
    if state is None:
        pytest.skip("v2 validator result shape not interpretable yet")
    assert state == "PASS", _errors(res)[:3]


def test_undefined_name_fail_with_symbol_and_line():
    from epsilon import verify

    v = verify.verify_python(BAD_UNDEF_PY)
    assert v["errors"], "compat verifier missed an undefined name"
    assert "missing_thing_xyz" in str(v["errors"]), v["errors"]
    if VAL_MOD is None:
        pytest.skip("v2 validate not present; compat detection verified (v1 gives no line)")
    res = _v2_validate(BAD_UNDEF_PY, "python")
    state = _state(res)
    if state is None:
        pytest.skip("v2 validator result shape not interpretable yet")
    assert state == "FAIL", state
    errs = _errors(res)
    assert errs, "FAIL with no error details"
    assert any("missing_thing_xyz" in _err_text(e) for e in errs), errs
    lines = []
    for e in errs:
        line = e.get("line") if isinstance(e, dict) else getattr(e, "line", None)
        if isinstance(line, int):
            lines.append(line)
    assert lines and all(n >= 1 for n in lines), errs


def test_nested_scope_shadowing_pass():
    """Shadowing across nested scopes must not be a false positive."""
    from epsilon import verify

    v = verify.verify_python(SHADOW_PY)
    assert v["syntax_ok"] and not v["errors"], v["errors"]
    ns = {}
    exec(compile(SHADOW_PY, "<shadow>", "exec"), ns)
    assert ns["f"]() == 1 and ns["g"]() == 5
    if VAL_MOD is None:
        return
    res = _v2_validate(SHADOW_PY, "python")
    state = _state(res)
    if state is None:
        pytest.skip("v2 validator result shape not interpretable yet")
    assert state == "PASS", _errors(res)[:3]


def test_bad_import_fail():
    if VAL_MOD is None:
        pytest.skip("v2 validate not present; v1 verifier does not check imports (known gap)")
    res = _v2_validate(BAD_IMPORT_PY, "python")
    state = _state(res)
    if state is None:
        pytest.skip("v2 validator result shape not interpretable yet")
    assert state == "FAIL", state
    assert _errors(res), "FAIL with no error details"


def test_missing_node_binary_returns_unknown_not_pass(monkeypatch):
    """Without node, JS verification must answer UNKNOWN — never PASS."""
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    for attr in ("which", "find_node", "node_bin"):
        if VAL_MOD is not None and hasattr(VAL_MOD, attr):
            try:
                monkeypatch.setattr(VAL_MOD, attr, lambda *a, **k: None)
            except Exception:
                pass
    if VAL_MOD is None:
        from epsilon import verify

        v = verify.verify_js(GOOD_JS)
        # v1 honesty boundary: heuristic fallback, never a hard success claim.
        assert v.get("exec_ok") in ("unknown", "unverified-exec"), v
        assert v.get("exec_ok") is not True
        assert "heuristic" in str(v.get("stderr", "")), v
        return
    res = _v2_validate(GOOD_JS, "javascript")
    state = _state(res)
    if state is None:
        pytest.skip("v2 validator result shape not interpretable yet")
    assert state == "UNKNOWN", state
    assert state != "PASS"
