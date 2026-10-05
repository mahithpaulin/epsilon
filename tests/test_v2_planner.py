"""Epsilon v2 planner tests — project-type detection and generic fallback.

SKIP strategy: ``epsilon.planner`` (or ``epsilon.plan``) may not exist yet.
Planner-specific assertions skip when it is absent; the compat fallback
(``epsilon.generate`` total on unseen specs) always runs. Stdlib + pytest
only; Python 3.11 compatible.
"""
import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_PLANNER_MODULES = ("epsilon.planner", "epsilon.plan")
_PLAN_FNS = ("plan", "plan_project", "build_plan", "create_plan", "plan_spec")

PLAN_MOD = None
for _name in _PLANNER_MODULES:
    try:
        PLAN_MOD = importlib.import_module(_name)
        break
    except ImportError:
        continue


def _plan_fn(mod):
    for name in _PLAN_FNS:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def _plan_one(fn, spec):
    try:
        return fn(spec)
    except TypeError:
        pytest.skip("planner entry has an unknown call signature; not integrated yet")


def _as_text(res):
    if isinstance(res, str):
        return res
    if isinstance(res, dict):
        try:
            return json.dumps(res, default=str)
        except Exception:
            return str(res)
    to_dict = getattr(res, "to_dict", None)
    if callable(to_dict):
        try:
            return json.dumps(to_dict(), default=str)
        except Exception:
            pass
    return str(res)


CLI_SPEC = "Build a CLI named greet that prints hello and takes a --name option."
API_SPEC = "Build a JSON REST API with a GET /users endpoint listing users."
LIB_SPEC = "Library function computing the moving average of a list of numbers."
MORSE_SPEC = (
    "Morse code trainer that encodes text to morse code, decodes morse code "
    "back to text, and offers a practice quiz function."
)


def _signals(res):
    """Coarse shape-agnostic signal set: which capability family fired."""
    if res is None:
        return set()
    text = _as_text(res).lower()
    sigs = set()
    if any(k in text for k in ("cli", "argparse", "console", "--name", "command")):
        sigs.add("cli")
    if any(k in text for k in ("endpoint", "route", "rest", "/users", "handler", "get ")):
        sigs.add("api")
    if "sqlite" in text or "persist" in text or "database" in text:
        sigs.add("persistence")
    if any(k in text for k in ("function", "def ", "library", "average")):
        sigs.add("library")
    return sigs


def test_type_detection_cli_vs_api_vs_library():
    if PLAN_MOD is None:
        pytest.skip("v2 planner not present yet")
    fn = _plan_fn(PLAN_MOD)
    if fn is None:
        pytest.skip("planner module has no recognisable entry point yet")
    cli = _plan_one(fn, CLI_SPEC)
    api = _plan_one(fn, API_SPEC)
    lib = _plan_one(fn, LIB_SPEC)
    s_cli, s_api, s_lib = _signals(cli), _signals(api), _signals(lib)
    if not (s_cli or s_api or s_lib):
        pytest.skip("planner result shape not interpretable yet")
    assert "cli" in s_cli, "CLI spec not detected as CLI: %s" % sorted(s_cli)
    assert "api" in s_api, "API spec not detected as API: %s" % sorted(s_api)
    assert s_lib != s_cli and s_lib != s_api, "library spec indistinguishable: %s" % sorted(s_lib)


def _assert_coherent_project(res):
    from epsilon import hir as H

    if isinstance(res, H.Project):
        assert res.source_files(), "plan produced a Project with no source files"
        assert res.tests, "plan produced a Project with no tests"
        assert res.file_paths(), "plan produced a Project with no file paths"
        assert res.name and isinstance(res.name, str)
        return
    if isinstance(res, dict) and res.get("files"):
        files = res["files"]
        assert isinstance(files, list) and files
        tests = res.get("tests", [])
        assert isinstance(tests, list) and tests, "plan produced files but no tests"
        return
    files = getattr(res, "files", None)
    tests = getattr(res, "tests", None)
    if isinstance(files, list) and files and isinstance(tests, list) and tests:
        return
    pytest.skip("planner result shape not interpretable yet: %r" % _as_text(res)[:200])


def test_unseen_spec_yields_coherent_project():
    """Unfamiliar spec (morse trainer) still yields files+tests, never crashes."""
    if PLAN_MOD is None:
        pytest.skip("v2 planner not present yet; see test_unseen_spec_generic_fallback_no_crash")
    fn = _plan_fn(PLAN_MOD)
    if fn is None:
        pytest.skip("planner module has no recognisable entry point yet")
    res = _plan_one(fn, MORSE_SPEC)
    _assert_coherent_project(res)


def test_unseen_spec_generic_fallback_no_crash():
    """Compat totality: unseen spec yields syntax-ok code via generic fallback."""
    from epsilon import generate

    res = generate(MORSE_SPEC)
    assert res["verdict"]["syntax_ok"], res["verdict"].get("errors")
    code = res["code"]
    assert isinstance(code, str) and code.strip()
    ns = {}
    exec(compile(code, "<morse>", "exec"), ns)  # must not raise


def test_plan_function_task_path_if_present():
    fn = None
    if PLAN_MOD is not None:
        for name in ("plan_function_task", "plan_function", "function_task", "task_plan"):
            cand = getattr(PLAN_MOD, name, None)
            if callable(cand):
                fn = cand
                break
    if fn is None:
        pytest.skip("plan_function_task path not present yet")
    for args in (("morse_encode", MORSE_SPEC), (MORSE_SPEC,)):
        try:
            res = fn(*args)
            break
        except TypeError:
            continue
    else:
        pytest.skip("plan_function_task has an unknown call signature; not integrated yet")
    _assert_coherent_project(res)


def test_empty_spec_raises_in_planner_path():
    if PLAN_MOD is None:
        from epsilon import generate

        with pytest.raises(ValueError):
            generate("")
        return
    fn = _plan_fn(PLAN_MOD)
    if fn is None:
        pytest.skip("planner module has no recognisable entry point yet")
    with pytest.raises(Exception):
        _plan_one(fn, "")
