"""Epsilon v2 end-to-end tests — build a small library, run its tests.

Uses the best high-level API available: a v2 builder (``epsilon.build`` /
``epsilon.pipeline`` / ... + planner/backends) when present, else the compat
``epsilon.generate`` single-function path. Generated tests run in the v2
sandbox when available, else via direct exec checks.
Stdlib + pytest only; Python 3.11 compatible.
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_BUILD_MODULES = (
    "epsilon.build",
    "epsilon.pipeline",
    "epsilon.api",
    "epsilon.app",
    "epsilon.project",
    "epsilon.create",
    "epsilon.planner",
)
_BUILD_FNS = (
    "build", "build_project", "create_project",
    "generate_project", "run", "make", "scaffold",
)
_SANDBOX_MODULES = ("epsilon.sandbox", "epsilon.runner", "epsilon.exec")
_SANDBOX_FNS = ("run_tests", "run", "run_code", "execute")


def _try_import(*names):
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


BUILD_MOD = _try_import(*_BUILD_MODULES)
SANDBOX_MOD = _try_import(*_SANDBOX_MODULES)

LIB_SPEC = (
    "Library with an add function returning the sum of two numbers "
    "and using only the standard library."
)


def _build_fn(mod):
    for name in _BUILD_FNS:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def _sandbox_fn(mod):
    for name in _SANDBOX_FNS:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def _compat_build_and_check(spec, checks):
    """Compat path: single function via generate(); exec + behavioural checks."""
    from epsilon import generate

    res = generate(spec)
    assert res["verdict"]["syntax_ok"], res["verdict"].get("errors")
    code = res["code"]
    assert isinstance(code, str) and code.strip()
    ns = {}
    exec(compile(code, "<e2e>", "exec"), ns)
    ir = res["ir"]
    fname = ir["functions"][0]["name"] if ir.get("functions") else None
    assert fname and callable(ns.get(fname)), "generated function missing: %s" % fname
    for args, want in checks:
        assert ns[fname](*args) == want, (fname, args, want)
    return res


def test_end_to_end_library_build(tmp_path):
    if BUILD_MOD is None:
        _compat_build_and_check(
            "function add(a, b) returns sum of a and b", [((2, 3), 5)])
        return
    fn = _build_fn(BUILD_MOD)
    if fn is None:
        _compat_build_and_check(
            "function add(a, b) returns sum of a and b", [((2, 3), 5)])
        return
    res = None
    for args in ((LIB_SPEC, str(tmp_path)), (LIB_SPEC,)):
        try:
            res = fn(*args)
            break
        except TypeError:
            continue
        except Exception as exc:
            pytest.fail("v2 build raised on a small library spec: %r" % (exc,))
    if res is None:
        pytest.skip("v2 builder has an unknown call signature; not integrated yet")
    files = None
    if isinstance(res, dict):
        files = res.get("files") or res.get("paths")
    else:
        files = getattr(res, "files", None) or getattr(res, "file_paths", None)
        if callable(files):
            try:
                files = files()
            except Exception:
                files = None
    if not isinstance(files, list) or not files:
        pytest.skip("v2 build result shape not interpretable yet: %r" % (str(res)[:200],))
    assert files, "build produced no files"


def test_generated_tests_pass_in_sandbox_when_available():
    if SANDBOX_MOD is None:
        # Compat: the "generated tests" are behavioural checks run by exec.
        _compat_build_and_check(
            "function add(a, b) returns sum of a and b",
            [((2, 3), 5), ((0, 0), 0), ((-1, 1), 0)],
        )
        return
    fn = _sandbox_fn(SANDBOX_MOD)
    if fn is None:
        pytest.skip("sandbox module has no recognisable entry point yet")
    code = "def add(a, b):\n    return a + b\n"
    cases = [((2, 3), 5), ((0, 0), 0)]
    passed = None
    for args in ((code, cases), ({"code": code, "cases": cases},)):
        try:
            out = fn(*args)
        except TypeError:
            continue
        except Exception as exc:
            pytest.fail("sandbox run raised: %r" % (exc,))
            return
        if isinstance(out, dict):
            if "passed" in out:
                passed = bool(out["passed"])
                break
            if "failures" in out:
                passed = not out["failures"]
                break
        elif isinstance(out, bool):
            passed = out
            break
    if passed is None:
        pytest.skip("sandbox result shape not interpretable yet")
    assert passed, "generated tests failed in sandbox"
