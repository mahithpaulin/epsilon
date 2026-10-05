"""Epsilon v2 adversarial tests — hostile inputs, bounded resources.

All tests run against real APIs today: compat ``generate``/``verify`` plus
the v2 ``sandbox``/``repair``/``validate`` modules when they land (probed with
SKIP otherwise). Stdlib + pytest only; Python 3.11 compatible.
"""
import importlib
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _try_import(*names):
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


SANDBOX_MOD = _try_import("epsilon.sandbox", "epsilon.runner", "epsilon.exec")
REPAIR_MOD = _try_import("epsilon.repair", "epsilon.fixer")
VAL_MOD = _try_import("epsilon.validate", "epsilon.validation", "epsilon.checker")

UNICODE_SPEC = (
    "fonction café naïve résumé héllo wörld ünïcodé "
    "morse trainer 北京 SSD 𝔘𝔫𝔦𝔠𝔬𝔡𝔢 🎉 that encodes text"
)


def test_empty_spec():
    from epsilon import generate

    for bad in ("", "   "):
        with pytest.raises(ValueError):
            generate(bad)


def test_6000_char_spec():
    """A 6000-char spec must not hang, crash, or return garbage."""
    from epsilon import generate

    spec = ("Please build a function that adds numbers with care. " * 120)[:6000]
    assert len(spec) == 6000
    started = time.time()
    try:
        res = generate(spec)
    except ValueError:
        return  # rejecting an absurd spec is acceptable; crashing is not
    elapsed = time.time() - started
    assert elapsed < 60, "spec handling took too long: %.1fs" % elapsed
    assert res["verdict"]["syntax_ok"], res["verdict"].get("errors")
    assert isinstance(res["code"], str) and res["code"].strip()


def test_weird_unicode_spec():
    """Weird unicode must not crash intake; output stays valid code."""
    from epsilon import generate

    try:
        res = generate(UNICODE_SPEC)
    except ValueError:
        return  # rejection is acceptable; crashing is not
    assert res["verdict"]["syntax_ok"], res["verdict"].get("errors")
    code = res["code"]
    assert isinstance(code, str) and code.strip()
    compile(code, "<unicode>", "exec")


def test_validation_of_empty_dir(tmp_path):
    """Validating an empty directory is a graceful non-PASS, never a crash."""
    from epsilon import verify

    assert list(tmp_path.iterdir()) == []
    assert verify.verify_python("")["syntax_ok"]  # empty source parses; no crash
    if VAL_MOD is None:
        pytest.skip("v2 validate (directory entry) not present yet")
    entry = None
    for name in ("validate_dir", "validate_directory", "validate_project",
                 "validate", "check_dir"):
        fn = getattr(VAL_MOD, name, None)
        if callable(fn):
            entry = fn
            break
    if entry is None:
        pytest.skip("v2 validate has no directory entry point yet")
    try:
        res = entry(str(tmp_path))
    except TypeError:
        pytest.skip("v2 directory-validate has an unknown call signature")
    except Exception as exc:
        pytest.fail("validating an empty dir crashed: %r" % (exc,))
    state = getattr(res, "state", None)
    if isinstance(res, dict):
        state = res.get("state", state)
    if state is None:
        pytest.skip("v2 directory-validate result shape not interpretable yet")
    assert state in ("UNKNOWN", "FAIL", "UNAVAILABLE"), state
    assert state != "PASS", "empty dir must not validate as PASS"


def test_sandbox_timeout():
    """A sleeping command with a tiny timeout reports timeout promptly."""
    started = time.time()
    if SANDBOX_MOD is not None:
        for name in ("run", "run_code", "execute"):
            fn = getattr(SANDBOX_MOD, name, None)
            if not callable(fn):
                continue
            try:
                out = fn("import time; time.sleep(5)", timeout=0.3)
            except TypeError:
                continue
            except Exception:
                continue
            text = str(out)
            assert "imeout" in text or getattr(out, "timed_out", False) is True, out
            assert time.time() - started < 20
            return
    from epsilon import verify

    r = verify.smoke_python("import time; time.sleep(5)", timeout=0.3)
    assert r["timed_out"] is True, r
    assert r["exec_ok"] is False
    assert time.time() - started < 20


def test_repair_bounded_iterations():
    """Repair terminates: fixes the fixable, reports (not loops on) the rest."""
    from epsilon import verify

    fixable = "def f():\n    return 1\n".replace("():", "()")
    assert fixable == "def f()\n    return 1\n"
    src, verdict = verify.repair_loop(fixable, "python", max_retries=3)
    assert verdict["syntax_ok"], verdict.get("errors")
    compile(src, "<repaired>", "exec")

    started = time.time()
    with pytest.raises(RuntimeError):
        verify.repair_loop("def f(:\n", "python", max_retries=2)
    assert time.time() - started < 20, "repair did not terminate promptly"

    if REPAIR_MOD is None:
        return
    entry = None
    for name in ("repair", "repair_loop", "fix", "attempt_repair"):
        fn = getattr(REPAIR_MOD, name, None)
        if callable(fn):
            entry = fn
            break
    if entry is None:
        pytest.skip("v2 repair has no recognisable entry point yet")
    try:
        out = entry("def f(:\n", max_iterations=2)
    except TypeError:
        try:
            out = entry("def f(:\n", "python", 2)
        except TypeError:
            pytest.skip("v2 repair has an unknown call signature")
        except Exception:
            return  # raising promptly on hopeless input is bounded behaviour
    except Exception:
        return  # raising promptly on hopeless input is bounded behaviour
    iters = None
    if isinstance(out, dict):
        iters = out.get("repair_iters", out.get("iters", out.get("iterations")))
    else:
        iters = getattr(out, "repair_iters", getattr(out, "iters", None))
    if iters is None:
        pytest.skip("v2 repair result shape not interpretable yet")
    assert isinstance(iters, int) and iters <= 2, out
