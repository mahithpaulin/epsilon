"""Epsilon v2 sandbox — safe subprocess execution. Stdlib only, deterministic."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from .errors import FAIL, PASS, UNAVAILABLE, UNKNOWN

MAX_OUTPUT = 20000
SMOKE_TIMEOUT_CAP = 10.0


def _tail(s: str | None, limit: int = MAX_OUTPUT) -> str:
    if not s:
        return ""
    if not isinstance(s, str):
        s = str(s)
    if len(s) > limit:
        return s[-limit:]
    return s


def _as_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (bytes, bytearray)):
        try:
            return bytes(v).decode("utf-8", errors="replace")
        except Exception:
            return str(v)
    return str(v)


@dataclass
class RunResult:
    argv: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    state: str
    duration_s: float


def _build_env(env_extra: dict | None, policy: str) -> dict:
    if policy == "open":
        env: dict[str, str] = dict(os.environ)
        if env_extra:
            for k, v in env_extra.items():
                if isinstance(k, str) and isinstance(v, str):
                    env[k] = v
        return env
    # restricted (default, also for unknown policies — safe default)
    base_keys = ("PATH", "HOME", "LANG", "SYSTEMROOT")
    env = {}
    for k in base_keys:
        val = os.environ.get(k)
        if isinstance(val, str) and val != "":
            env[k] = val
    if not env.get("PATH"):
        env["PATH"] = os.defpath
    if env_extra:
        for k, v in env_extra.items():
            if isinstance(k, str) and isinstance(v, str):
                env[k] = v
    return env


def run(argv, cwd, timeout, env_extra=None, policy="restricted") -> RunResult:
    """Run argv in cwd with timeout. Never uses shell=True.

    Restricted policy builds a minimal env (PATH, HOME, LANG, SYSTEMROOT
    plus env_extra). Open policy inherits os.environ plus env_extra.
    Empty argv[0] or missing cwd -> UNKNOWN (refusal, no spawn).
    Timeout -> timed_out True, state FAIL. Nonzero exit -> FAIL.
    Outputs are tail-capped at MAX_OUTPUT chars.
    """
    argv_list = list(argv) if argv else []
    cwd_s = str(cwd) if cwd is not None else ""
    # Refusals — no spawn.
    if not argv_list or not argv_list[0] or (isinstance(argv_list[0], str) and argv_list[0].strip() == ""):
        return RunResult(
            argv=[str(a) for a in argv_list],
            cwd=cwd_s,
            exit_code=None,
            stdout="",
            stderr="refused: empty argv[0]",
            timed_out=False,
            state=UNKNOWN,
            duration_s=0.0,
        )
    if not cwd_s or not os.path.isdir(cwd_s):
        return RunResult(
            argv=[str(a) for a in argv_list],
            cwd=cwd_s,
            exit_code=None,
            stdout="",
            stderr="refused: cwd missing: %r" % (cwd_s,),
            timed_out=False,
            state=UNKNOWN,
            duration_s=0.0,
        )
    try:
        timeout_f = float(timeout)
    except (TypeError, ValueError):
        return RunResult(
            argv=[str(a) for a in argv_list],
            cwd=cwd_s,
            exit_code=None,
            stdout="",
            stderr="refused: bad timeout %r" % (timeout,),
            timed_out=False,
            state=UNKNOWN,
            duration_s=0.0,
        )
    if timeout_f <= 0 or timeout_f > 600:
        return RunResult(
            argv=[str(a) for a in argv_list],
            cwd=cwd_s,
            exit_code=None,
            stdout="",
            stderr="refused: timeout out of range (0, 600]: %r" % (timeout_f,),
            timed_out=False,
            state=UNKNOWN,
            duration_s=0.0,
        )
    norm_policy = policy if policy in ("restricted", "open") else "restricted"
    env = _build_env(env_extra if isinstance(env_extra, dict) else None, norm_policy)
    argv_strs = [str(a) for a in argv_list]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv_strs,
            cwd=cwd_s,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_f,
            shell=False,
            check=False,
        )
        dur = time.monotonic() - start
        out = _tail(_as_str(proc.stdout))
        err_s = _tail(_as_str(proc.stderr))
        state = PASS if proc.returncode == 0 else FAIL
        return RunResult(
            argv=argv_strs,
            cwd=cwd_s,
            exit_code=proc.returncode,
            stdout=out,
            stderr=err_s,
            timed_out=False,
            state=state,
            duration_s=dur,
        )
    except subprocess.TimeoutExpired as e:
        dur = time.monotonic() - start
        out = _tail(_as_str(getattr(e, "stdout", "")))
        err_s = _tail(_as_str(getattr(e, "stderr", "")))
        note = "timeout after %.1fs" % timeout_f
        err_s = _tail((err_s + "\n" + note).strip() if err_s else note)
        return RunResult(
            argv=argv_strs,
            cwd=cwd_s,
            exit_code=None,
            stdout=out,
            stderr=err_s,
            timed_out=True,
            state=FAIL,
            duration_s=dur,
        )
    except FileNotFoundError as e:
        dur = time.monotonic() - start
        return RunResult(
            argv=argv_strs,
            cwd=cwd_s,
            exit_code=None,
            stdout="",
            stderr="spawn failed: %s" % e,
            timed_out=False,
            state=UNKNOWN,
            duration_s=dur,
        )
    except OSError as e:
        dur = time.monotonic() - start
        return RunResult(
            argv=argv_strs,
            cwd=cwd_s,
            exit_code=None,
            stdout="",
            stderr="spawn failed: %s" % e,
            timed_out=False,
            state=UNKNOWN,
            duration_s=dur,
        )


def tool_available(name) -> bool:
    """shutil.which wrapper. False on empty/invalid input, never raises."""
    try:
        if not isinstance(name, str) or not name.strip():
            return False
        return shutil.which(name) is not None
    except Exception:
        return False


def _parse_unittest_output(text: str):
    """Parse 'Ran N tests' + trailing OK/FAILED. Returns (passed, failed, total, ok)."""
    import re

    total = 0
    m_ran = None
    for m in re.finditer(r"Ran\s+(\d+)\s+tests?", text):
        m_ran = m
    if m_ran is not None:
        try:
            total = int(m_ran.group(1))
        except ValueError:
            total = 0
    ok = bool(re.search(r"^OK\b", text, re.M))
    failed_mark = bool(re.search(r"^FAILED\b", text, re.M))
    passed = 0
    failed = 0
    if m_ran is None:
        return (0, 0, 0, False)
    if ok and not failed_mark:
        passed = total
        failed = 0
        return (passed, failed, total, True)
    if failed_mark:
        mf = re.search(r"failures=(\d+)", text)
        me = re.search(r"errors=(\d+)", text)
        nf = int(mf.group(1)) if mf else 0
        ne = int(me.group(1)) if me else 0
        if nf == 0 and ne == 0:
            # FAILED without counts: assume all (or 1 if total==0).
            failed = total if total > 0 else 1
        else:
            failed = nf + ne
        if total > 0:
            failed = min(failed, total) if failed > 0 else min(1, total)
            passed = max(0, total - failed)
        else:
            passed = 0
            failed = max(1, failed)
        return (passed, failed, total, False)
    # Ran line but neither OK nor FAILED (e.g. interrupted) -> treat as no pass.
    return (0, total if total else 0, total, False)


def run_tests(out_dir, language, timeout) -> dict:
    """Run the test suite in out_dir. Never claims PASS without runner output.

    python: [sys.executable, -m, unittest, discover, -s, tests|test|.]
    javascript/typescript: node --test (UNAVAILABLE when node missing).
    Returns {state, passed, failed, total, stdout, stderr}.
    """
    lang = str(language or "").lower().strip()
    out_s = str(out_dir) if out_dir is not None else ""
    if not out_s or not os.path.isdir(out_s):
        return {
            "state": UNKNOWN,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "stdout": "",
            "stderr": "refused: out_dir missing: %r" % (out_s,),
        }
    try:
        timeout_f = float(timeout)
    except (TypeError, ValueError):
        return {
            "state": UNKNOWN,
            "passed": 0,
            "failed": 0,
            "total": 0,
            "stdout": "",
            "stderr": "refused: bad timeout %r" % (timeout,),
        }
    if lang in ("python", "py"):
        if os.path.isdir(os.path.join(out_s, "tests")):
            start_dir = "tests"
        elif os.path.isdir(os.path.join(out_s, "test")):
            start_dir = "test"
        else:
            start_dir = "."
        argv = [sys.executable, "-m", "unittest", "discover", "-s", start_dir]
        res = run(argv, out_s, timeout_f)
        combined = (res.stdout or "") + "\n" + (res.stderr or "")
        passed, failed, total, ok = _parse_unittest_output(combined)
        if total > 0 and ok and res.exit_code == 0 and not res.timed_out:
            state = PASS
        elif total > 0:
            state = FAIL
        elif total == 0 and combined.strip():
            # Ran 0 tests (empty dir) or crash output: no evidence -> UNKNOWN
            # unless the runner clearly errored (nonzero exit with traceback).
            if res.timed_out:
                state = FAIL
            elif res.exit_code not in (None, 0):
                # unittest errors before discovery still produce output;
                # without a Ran line we cannot count, but it is a failure.
                # Keep FAIL only if there is real output, else UNKNOWN.
                state = FAIL
            else:
                state = UNKNOWN
        else:
            state = UNKNOWN
            if res.timed_out:
                state = FAIL
        return {
            "state": state,
            "passed": passed,
            "failed": failed,
            "total": total,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    if lang in ("javascript", "js", "typescript", "ts"):
        import re

        if not tool_available("node"):
            return {
                "state": UNAVAILABLE,
                "passed": 0,
                "failed": 0,
                "total": 0,
                "stdout": "",
                "stderr": "node not found",
            }
        node = shutil.which("node") or "node"
        # Pass explicit test files: `node --test <dir>` tries to LOAD the
        # directory as a module instead of discovering inside it.
        test_files = []
        for sub in ("tests", "test"):
            d = os.path.join(out_s, sub)
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if fn.endswith(('.test.js', '.test.ts', '.test.mjs',
                                    '_test.js')) or (
                                        fn.startswith('test') and
                                        fn.endswith(('.js', '.ts'))):
                        test_files.append(os.path.join(sub, fn))
                break
        argv = [node, "--test"] + test_files if test_files else [node, "--test"]
        res = run(argv, out_s, timeout_f)
        combined = (res.stdout or "") + "\n" + (res.stderr or "")
        # node --test summary: old TAP '# pass N' / '# fail N', new
        # 'ℹ pass N' / 'ℹ fail N' (node >=18). Accept either.
        mp = None
        mf = None
        for m in re.finditer(r"[#\u2139i]\s*pass\s+(\d+)", combined):
            mp = m
        for m in re.finditer(r"[#\u2139i]\s*fail\s+(\d+)", combined):
            mf = m
        if mp is not None and mf is not None:
            try:
                passed = int(mp.group(1))
            except ValueError:
                passed = 0
            try:
                failed = int(mf.group(1))
            except ValueError:
                failed = 0
            total = passed + failed
            if total == 0:
                state = UNKNOWN
            elif failed == 0 and passed > 0 and res.exit_code == 0 and not res.timed_out:
                state = PASS
            else:
                state = FAIL
        else:
            passed, failed, total = 0, 0, 0
            if res.timed_out:
                state = FAIL
            elif res.exit_code not in (None, 0):
                state = FAIL
            else:
                # Exit 0 but no parseable counts: cannot claim PASS.
                state = UNKNOWN
        return {
            "state": state,
            "passed": passed,
            "failed": failed,
            "total": total,
            "stdout": res.stdout,
            "stderr": res.stderr,
        }
    return {
        "state": UNKNOWN,
        "passed": 0,
        "failed": 0,
        "total": 0,
        "stdout": "",
        "stderr": "unsupported language %r" % (language,),
    }


def check_smoke(out_dir, entry, language, timeout) -> dict:
    """Run entry --help with a short timeout. Missing entry -> UNKNOWN."""
    lang = str(language or "").lower().strip()
    out_s = str(out_dir) if out_dir is not None else ""
    entry_s = str(entry) if entry is not None else ""
    if not entry_s.strip():
        return {
            "state": UNKNOWN,
            "exit_code": None,
            "stdout": "",
            "stderr": "missing entry",
            "timed_out": False,
            "entry": entry_s,
        }
    if os.path.isabs(entry_s):
        full = entry_s
    else:
        full = os.path.join(out_s or ".", entry_s)
    if not os.path.isfile(full):
        return {
            "state": UNKNOWN,
            "exit_code": None,
            "stdout": "",
            "stderr": "missing entry file: %r" % (entry_s,),
            "timed_out": False,
            "entry": entry_s,
        }
    try:
        timeout_f = float(timeout)
    except (TypeError, ValueError):
        timeout_f = SMOKE_TIMEOUT_CAP
    effective = min(max(timeout_f, 0.1), SMOKE_TIMEOUT_CAP)
    if lang in ("python", "py"):
        argv = [sys.executable, full, "--help"]
        cwd = out_s if out_s and os.path.isdir(out_s) else (os.path.dirname(full) or ".")
        res = run(argv, cwd, effective)
        return {
            "state": res.state,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "timed_out": res.timed_out,
            "entry": entry_s,
        }
    if lang in ("javascript", "js", "typescript", "ts"):
        if not tool_available("node"):
            return {
                "state": UNAVAILABLE,
                "exit_code": None,
                "stdout": "",
                "stderr": "node not found",
                "timed_out": False,
                "entry": entry_s,
            }
        node = shutil.which("node") or "node"
        argv = [node, full, "--help"]
        cwd = out_s if out_s and os.path.isdir(out_s) else (os.path.dirname(full) or ".")
        res = run(argv, cwd, effective)
        return {
            "state": res.state,
            "exit_code": res.exit_code,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "timed_out": res.timed_out,
            "entry": entry_s,
        }
    return {
        "state": UNKNOWN,
        "exit_code": None,
        "stdout": "",
        "stderr": "unsupported language %r" % (language,),
        "timed_out": False,
        "entry": entry_s,
    }
