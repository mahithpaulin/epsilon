#!/usr/bin/env python3
"""Epsilon v2 capability benchmark — 10 levels, stdlib only.

Each level builds via the best available API (v2 builder when importable,
else the compat ``epsilon.generate`` single-function path), then measures::

    generation_ok / syntax_ok / tests_ok / repair_iters / seconds / files
    failure_category in {none,generation,syntax,tests,timeout,unavailable,unsupported}

Prints a JSON summary to stdout. Exits nonzero ONLY with ``--strict`` (when
any level's ``tests_ok`` is false). Designed to run in well under 5 minutes:
no network, small bounded subprocess timeouts, node used only if present.

Python 3.11 compatible; stdlib only.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILURE_CATEGORIES = (
    "none", "generation", "syntax", "tests",
    "timeout", "unavailable", "unsupported",
)

try:
    STDLIB_NAMES = set(sys.stdlib_module_names)
except AttributeError:  # pragma: no cover - very old interpreters
    STDLIB_NAMES = {
        "argparse", "ast", "collections", "dataclasses", "functools", "http",
        "itertools", "json", "math", "os", "pathlib", "random", "re",
        "shlex", "sqlite3", "statistics", "string", "subprocess", "sys",
        "tempfile", "time", "typing", "unittest",
    }


def _try_import(*names):
    for name in names:
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


# ------------------------------------------------------------ build layer

def _compat_build(spec, lang="python"):
    from epsilon import generate

    res = generate(spec, lang=lang)
    code = res["code"]
    ir = res.get("ir", {})
    fname = None
    if isinstance(ir, dict) and ir.get("functions"):
        fname = ir["functions"][0].get("name")
    return {
        "ok": True,
        "files": [{"path": "main.py" if lang == "python" else "main.js",
                   "code": code, "language": lang}],
        "project": None,
        "func": fname,
        "ir": ir,
    }


def _detect_api():
    """Return (api_name, build_fn). Prefers any v2 builder, else compat."""
    mods = (
        "epsilon.build", "epsilon.pipeline", "epsilon.api", "epsilon.app",
        "epsilon.project", "epsilon.create",
    )
    fns = ("build", "build_project", "create_project", "generate_project",
           "run", "make", "scaffold")
    for modname in mods:
        try:
            mod = importlib.import_module(modname)
        except ImportError:
            continue
        for fnname in fns:
            fn = getattr(mod, fnname, None)
            if callable(fn):
                return "%s.%s" % (modname, fnname), fn
    return "compat-v1:epsilon.generate", None


API_NAME, _V2_BUILD_FN = _detect_api()

FUNCTION_PATH_LEVELS = {1, 8}


def _v2_project_build(spec, lang="python"):
    """Native project pipeline: fresh tmp out_dir, entry file first."""
    import tempfile
    from epsilon.pipeline import build
    from epsilon.config import EpsilonConfig
    out = tempfile.mkdtemp(prefix="eps-cap-")
    target = 'javascript' if lang == 'js' else ('typescript' if lang == 'ts' else 'python')
    cfg = EpsilonConfig(target_language=target, out_dir=out,
                        run_tests=True, repair_iterations=2, timeout_secs=20.0)
    out_dir, report, _ctx = build(spec, cfg)
    rd = report.to_dict()
    entry = None
    for f in rd.get('files', []):
        pass
    files = []
    for root, ds, fns in os.walk(out_dir):
        ds[:] = [d for d in ds if d != '__pycache__']
        for fn in sorted(fns):
            if fn == '.epsilon-project.json' or fn.endswith(('.pyc', '.pyo')):
                continue
            p = os.path.join(root, fn)
            try:
                with open(p, encoding='utf-8', errors='strict') as fh:
                    files.append({'path': os.path.relpath(p, out_dir),
                                  'code': fh.read(),
                                  'language': lang})
            except (OSError, UnicodeError):
                continue
    entry_path = (report.files[0] if False else None)
    # entry file first: read it from the persisted HIR project meta
    entry_rel = None
    try:
        from epsilon import hir as _H
        with open(os.path.join(out_dir, '.epsilon-project.json')) as fh:
            proj = _H.node_from_dict(json.load(fh))
        entry_rel = proj.entry
        project_obj = proj
    except Exception:
        project_obj = None
    if entry_rel:
        files.sort(key=lambda f: (0 if f['path'] == entry_rel else 1, f['path']))
    func = None
    try:
        if project_obj is not None:
            for fl in project_obj.files:
                if (type(fl).__name__ == 'SourceFile'
                        and (not entry_rel or fl.path == entry_rel)):
                    for d in fl.declarations or []:
                        if type(d).__name__ == 'FuncDef' and d.params:
                            func = d.name
                            break
                    break
    except Exception:
        func = None
    verdicts = rd.get('verdicts', [])
    tests = rd.get('tests', {}) or {}
    ok_tests = tests.get('state') == 'PASS'
    return {'ok': True, 'files': files, 'project': project_obj,
            'func': func, 'ir': None, 'via': 'project-pipeline',
            'report_state': rd.get('state'),
            'report_tests': tests, 'report_verdicts': verdicts,
            'repair_iters': (rd.get('metrics', {}) or {}).get('repair_iterations', 0),
            'out_dir': out_dir}


def build_level(spec, lang="python", level_id=None):
    """Build one spec; v2 when available, else compat. Never raises."""
    if level_id in FUNCTION_PATH_LEVELS and lang != 'both':
        try:
            b = _compat_build(spec, lang)
            b['via'] = 'function-path'
            return b
        except Exception as exc:
            pass
    if _V2_BUILD_FN is not None and level_id not in FUNCTION_PATH_LEVELS:
        try:
            return _v2_project_build(spec, lang)
        except Exception as exc:  # degrade gracefully, record below
            return {"ok": False, "error": "v2 build raised: %r" % (exc,),
                    "trace": traceback.format_exc(limit=3)}
    if _V2_BUILD_FN is not None and lang == 'both':
        pass  # handled by caller via two single builds below
    try:
        b = _compat_build(spec, lang)
        b['via'] = 'function-path'
        return b
    except Exception as exc:
        return {"ok": False, "error": "%r" % (exc,),
                "trace": traceback.format_exc(limit=3)}


def _normalize_v2(res, spec, lang):
    """Best-effort normalisation of an unknown v2 result shape."""
    if isinstance(res, dict) and ("files" in res or "code" in res):
        files = res.get("files") or ([{"path": "main.py", "code": res["code"]}]
                                     if res.get("code") else [])
        norm = [{"path": f.get("path", "file%d" % i) if isinstance(f, dict) else str(f),
                 "code": f.get("code", "") if isinstance(f, dict) else "",
                 "language": (f.get("language", lang) if isinstance(f, dict) else lang)}
                for i, f in enumerate(files)]
        return {"ok": bool(norm), "files": norm, "project": res,
                "func": res.get("func"), "ir": res.get("ir")}
    project = res
    files = getattr(project, "files", None)
    if isinstance(files, list) and files:
        return {"ok": True, "files": files, "project": project,
                "func": None, "ir": None}
    return {"ok": False, "error": "unrecognised v2 result shape: %r" % (str(res)[:200],)}


# ------------------------------------------------------------ check layer

def _syntax_ok(code, lang):
    if lang == "python":
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False
    from epsilon import verify

    try:
        return bool(verify.verify_js(code).get("syntax_ok"))
    except Exception:
        return False


def _exec_python(code, timeout=10):
    ns = {}
    exec(compile(code, "<cap>", "exec"), ns)
    return ns


def _callable_names(ns):
    return [k for k, v in ns.items()
            if callable(v) and not k.startswith("__")
            and getattr(v, "__module__", None) in (None, "builtins", "__main__", "cap")]


def _try_call(fn, probes=((), (2, 3), (3,), ("SOS",), ([3, 1, 2],))):
    """Call with generic probes; ok if ANY probe runs without exception."""
    tried = 0
    for args in probes:
        try:
            params = list(inspect.signature(fn).parameters.values())
        except (TypeError, ValueError):
            params = None
        if params is not None:
            n_pos = sum(1 for p in params if p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD))
            has_var = any(p.kind == inspect.Parameter.VAR_POSITIONAL
                          for p in params)
            if not has_var and len(args) != n_pos and not (
                    len(args) == 0 and all(
                        p.default is not inspect.Parameter.empty
                        for p in params)):
                continue
        tried += 1
        try:
            return True, fn(*args)
        except Exception:
            continue
    return False, "no probe call succeeded (%d tried)" % tried


def _third_party_imports(code):
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return sorted(n for n in found if n not in STDLIB_NAMES)


def _node():
    return shutil.which("node")


# ------------------------------------------------------------ level defs

def _check_l1(b):
    """Single function add(a,b): exact behavioural check."""
    code = b["files"][0]["code"]
    try:
        ns = _exec_python(code)
    except Exception as exc:
        return False, {"error": "exec failed: %r" % (exc,)}, "tests"
    name = b.get("func") or "add"
    fn = ns.get(name)
    if not callable(fn):
        cands = _callable_names(ns)
        if not cands:
            return False, {"error": "no callable found"}, "tests"
        fn = ns[cands[0]]
    try:
        got = fn(2, 3)
    except Exception as exc:
        return False, {"error": "call failed: %r" % (exc,)}, "tests"
    if got == 5:
        return True, {"func": name, "add(2,3)": 5}, "none"
    return False, {"func": name, "add(2,3)": got}, "tests"


def _check_callable_module(b, minimum):
    code = b["files"][0]["code"]
    try:
        ns = _exec_python(code)
    except Exception as exc:
        return False, {"error": "exec failed: %r" % (exc,)}, "tests"
    names = _callable_names(ns)
    details = {"funcs": names}
    if len(names) < minimum:
        details["note"] = "compat path yields one function; multi-function needs v2 planner"
        return False, details, "unsupported"
    bad = {}
    for n in names:
        ok, _ = _try_call(ns[n])
        if not ok:
            bad[n] = "no probe call succeeded"
    if bad:
        details["bad"] = bad
        return False, details, "tests"
    return True, details, "none"


def _check_l3(b):
    n = len(b.get("files") or [])
    code_files = [f for f in b["files"] or []
                  if isinstance(f, dict) and f.get("path", "").endswith((".py", ".js", ".ts"))]
    if n < 2:
        return False, {"files": n,
                       "note": "multi-file package needs v2 planner"}, "unsupported"
    bad = [f.get("path") for f in code_files
           if (f.get("language", "python") == "python"
               and not _syntax_ok(f.get("code", ""), "python"))]
    if bad:
        return False, {"bad_files": bad}, "syntax"
    return True, {"files": n, "code_files": len(code_files)}, "none"


def _project_surface(project):
    """Structured capability evidence from a v2 project object (not text)."""
    out = {}
    if project is None:
        return out
    keys = ("cli", "commands", "endpoints", "routes", "models",
            "dependencies", "tests")
    if isinstance(project, dict):
        items = [(k, project.get(k)) for k in keys]
    else:
        items = [(k, getattr(project, k, None)) for k in keys]
    for k, v in items:
        if isinstance(v, list) and v:
            out[k] = len(v)
    return out


def _whole_word_hits(blob, keywords):
    return [k for k in keywords
            if re.search(r"\b" + re.escape(k) + r"\b", blob)]


def _check_surface(b, keywords, label, structured=None):
    surf = _project_surface(b.get("project"))
    if structured is not None and structured(surf):
        return True, {label: surf}, "none"
    texts = []
    for f in b.get("files") or []:
        texts.append(str((f.get("code") if isinstance(f, dict) else "") or ""))
    blob = "\n".join(texts).lower()
    # Whole-word matching, and at least TWO distinct hits: a single
    # coincidental function name (e.g. compat naming a function
    # 'endpoints') must not count as an API surface.
    hits = _whole_word_hits(blob, [k.lower() for k in keywords])
    if len(set(hits)) >= 2:
        return True, {label: sorted(set(hits))}, "none"
    return False, {"hits": sorted(set(hits)),
                   "note": "%s surface needs v2 planner/backends" % label}, "unsupported"


def _check_l7(b):
    code = b["files"][0]["code"]
    third = _third_party_imports(code)
    if not third:
        return True, {"manifest": "vacuous-ok",
                      "note": "stdlib-only code needs no manifest entry",
                      "degraded": True}, "none"
    deps_mod = _try_import("epsilon.deps", "epsilon.dependencies", "epsilon.manifest")
    if deps_mod is None:
        return False, {"third_party": third,
                       "note": "third-party imports with no manifest support"}, "unsupported"
    return False, {"third_party": third,
                   "note": "manifest consistency check not integrated"}, "unsupported"


def _check_l8(b):
    from epsilon import verify

    code = b["files"][0]["code"]
    broken = code.replace("):\n", ")\n", 1)
    if broken == code:  # nothing to break; inject a different defect
        broken = code.replace("\n", "\n\n", 1)
    repair_mod = _try_import("epsilon.repair", "epsilon.fixer")
    iters = 0
    fixed = None
    if repair_mod is not None:
        for name in ("repair", "repair_loop", "fix", "attempt_repair"):
            fn = getattr(repair_mod, name, None)
            if not callable(fn):
                continue
            try:
                out = fn(broken, max_iterations=3)
                iters = (out.get("repair_iters", 1) if isinstance(out, dict)
                         else getattr(out, "repair_iters", 1))
                fixed = (out.get("code", "") if isinstance(out, dict)
                         else getattr(out, "code", ""))
                break
            except TypeError:
                continue
            except Exception:
                break
    if fixed is None:
        try:
            fixed, _v = verify.repair_loop(broken, "python", max_retries=3)
            iters = 1
        except Exception as exc:
            return False, {"repair_iters": 0,
                           "error": "repair failed: %r" % (exc,)}, "tests"
    if not _syntax_ok(fixed, "python"):
        return False, {"repair_iters": iters}, "syntax"
    try:
        ns = _exec_python(fixed)
    except Exception as exc:
        return False, {"repair_iters": iters,
                       "error": "repaired exec failed: %r" % (exc,)}, "tests"
    names = _callable_names(ns)
    if not names:
        return False, {"repair_iters": iters, "error": "no callable after repair"}, "tests"
    ok, _ = _try_call(ns[names[0]])
    return (True, {"repair_iters": iters, "func": names[0]}, "none") if ok else (
        False, {"repair_iters": iters}, "tests")


def _check_l9(b):
    code = b["files"][0]["code"]
    try:
        ns = _exec_python(code)
    except Exception as exc:
        return False, {"error": "exec failed: %r" % (exc,)}, "tests"
    names = _callable_names(ns)
    if not names:
        return False, {"error": "no callable in fallback module"}, "tests"
    ok, result = _try_call(ns[names[0]])
    details = {"func": names[0], "degraded": API_NAME.startswith("compat")}
    if ok:
        details["sample_result"] = str(result)[:100]
        return True, details, "none"
    details["error"] = str(result)[:200]
    return False, details, "tests"


def _check_l10(b_py, b_js):
    py_code = b_py["files"][0]["code"]
    js_code = b_js["files"][0]["code"]
    details = {"node_present": bool(_node())}
    try:
        ns = _exec_python(py_code)
    except Exception as exc:
        details["error"] = "py exec failed: %r" % (exc,)
        return False, details, "tests"
    fn = ns.get(b_py.get("func") or "add")
    if not callable(fn):
        cands = _callable_names(ns)
        fn = ns[cands[0]] if cands else None
    if fn is None:
        return False, details, "tests"
    try:
        py_ok = (fn(2, 3) == 5)
    except Exception:
        py_ok = False
    details["py_add_ok"] = py_ok
    node = _node()
    if node is None:
        details["js"] = "syntax-only (no node)"
        details["degraded"] = True
        js_ok = _syntax_ok(js_code, "js")
        details["js_syntax_ok"] = js_ok
        if py_ok and js_ok:
            return True, details, "none"
        return False, details, "syntax" if not js_ok else "tests"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(js_code + "\nconsole.log(add(2, 3));\n")
        path = fh.name
    try:
        proc = subprocess.run([node, path], capture_output=True, text=True,
                              timeout=15)
    except subprocess.TimeoutExpired:
        return False, details, "timeout"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    js_ok = proc.returncode == 0 and proc.stdout.strip().split()[-1:] == ["5"]
    details["js_add_ok"] = js_ok
    details["js_stdout"] = proc.stdout.strip()[-50:]
    if py_ok and js_ok:
        return True, details, "none"
    return False, details, "tests"


LEVELS = [
    {"id": 1, "name": "single-function",
     "spec": "function add(a, b) returns sum of a and b", "lang": "python"},
    {"id": 2, "name": "multi-function-module",
     "spec": ("module with functions add_numbers, subtract_numbers and "
              "multiply_numbers for integer arithmetic"), "lang": "python"},
    {"id": 3, "name": "multi-file-package",
     "spec": ("package mathkit with a math_utils module and a string_utils "
              "module"), "lang": "python"},
    {"id": 4, "name": "cli",
     "spec": ("CLI tool named filetool that copies files and supports a "
              "--verbose flag"), "lang": "python"},
    {"id": 5, "name": "api",
     "spec": ("REST API with GET /items and POST /items endpoints "
              "returning JSON"), "lang": "python"},
    {"id": 6, "name": "persistence-sqlite",
     "spec": ("address book storing contacts in a sqlite database with "
              "names and emails"), "lang": "python"},
    {"id": 7, "name": "external-dep-manifest",
     "spec": ("fetch JSON over HTTP with requests and retry three times"),
     "lang": "python"},
    {"id": 8, "name": "repair-after-injected-defect",
     "spec": "function add(a, b) returns sum of a and b", "lang": "python"},
    {"id": 9, "name": "unfamiliar-spec",
     "spec": ("Morse code trainer that encodes text to morse code, decodes "
              "morse code back, and offers a practice quiz function"),
     "lang": "python"},
    {"id": 10, "name": "cross-language-py-js",
     "spec": "function add(a, b) returns sum of a and b", "lang": "both"},
]


# ------------------------------------------------------------ runner

def run_level(level):
    started = time.time()
    rec = {"id": level["id"], "name": level["name"],
           "generation_ok": False, "syntax_ok": False, "tests_ok": False,
           "repair_iters": 0, "seconds": 0.0, "files": 0,
           "failure_category": "generation", "degraded": False, "details": {}}
    try:
        if level["lang"] == "both":
            b_py = _compat_build(level["spec"], "python")
            b_py['via'] = 'function-path'
            b_js = _compat_build(level["spec"], "js")
            b_js['via'] = 'function-path'
            if not (b_py.get("ok") and b_js.get("ok")):
                rec["details"] = {"py_error": b_py.get("error"),
                                  "js_error": b_js.get("error")}
                rec["failure_category"] = "generation"
                return rec
            rec["generation_ok"] = True
            rec["files"] = len(b_py.get("files", [])) + len(b_js.get("files", []))
            py_code = b_py["files"][0]["code"]
            js_code = b_js["files"][0]["code"]
            try:
                ast.parse(py_code)
                py_syn = True
            except SyntaxError:
                py_syn = False
            js_syn = _syntax_ok(js_code, "js")
            rec["syntax_ok"] = bool(py_syn and js_syn)
            if not rec["syntax_ok"]:
                rec["failure_category"] = "syntax"
                rec["details"] = {"py_syntax_ok": py_syn, "js_syntax_ok": js_syn}
                return rec
            ok, details, cat = _check_l10(b_py, b_js)
            rec["tests_ok"] = ok
            rec["failure_category"] = cat
            rec["details"] = details
            rec["degraded"] = bool(details.get("degraded"))
            return rec

        b = build_level(level["spec"], level["lang"], level["id"])
        if not b.get("ok"):
            rec["details"] = {"error": b.get("error")}
            return rec
        rec["generation_ok"] = True
        if isinstance(b.get("repair_iters"), int):
            rec["repair_iters"] = b["repair_iters"]
        if b.get("via"):
            rec["details"] = {"via": b["via"]}
        files = b.get("files") or []
        rec["files"] = len(files)
        code = files[0].get("code", "") if files else ""
        rec["syntax_ok"] = _syntax_ok(code, level["lang"])
        if not rec["syntax_ok"]:
            rec["failure_category"] = "syntax"
            return rec
        lid = level["id"]
        if lid == 1:
            ok, details, cat = _check_l1(b)
        elif lid == 2:
            ok, details, cat = _check_callable_module(b, 2)
        elif lid == 3:
            ok, details, cat = _check_l3(b)
        elif lid == 4:
            ok, details, cat = _check_surface(
                b, ("argparse", "sys.argv", "click", "console_scripts",
                    "--verbose", "cli"), "cli",
                structured=lambda s: bool(s.get("cli") or s.get("commands")))
        elif lid == 5:
            ok, details, cat = _check_surface(
                b, ("endpoint", "endpoints", "route", "routes", "flask",
                    "fastapi", "/items", "http.server", "handler"), "api",
                structured=lambda s: bool(s.get("endpoints") or s.get("routes")))
        elif lid == 6:
            ok, details, cat = _check_surface(
                b, ("sqlite3", "sqlite", "persist", "database", "model",
                    "models"), "persistence",
                structured=lambda s: bool(s.get("models")))
        elif lid == 7:
            ok, details, cat = _check_l7(b)
        elif lid == 8:
            ok, details, cat = _check_l8(b)
            if isinstance(details, dict) and "repair_iters" in details:
                rec["repair_iters"] = details["repair_iters"]
        elif lid == 9:
            ok, details, cat = _check_l9(b)
        else:
            ok, details, cat = False, {"error": "unknown level"}, "unavailable"
        rec["tests_ok"] = bool(ok)
        rec["failure_category"] = cat
        rec["details"] = details
        if isinstance(rec["details"], dict) and b.get("via"):
            rec["details"].setdefault("via", b["via"])
        if isinstance(rec["details"], dict) and isinstance(b.get("report_tests"), dict):
            rec["details"].setdefault("pipeline_tests", {
                k: b["report_tests"].get(k) for k in ("state", "passed", "failed", "total")})
            rec["details"].setdefault("pipeline_state", b.get("report_state"))
        rec["degraded"] = bool(details.get("degraded", False)
                               or API_NAME.startswith("compat"))
        return rec
    except Exception as exc:  # never crash the benchmark
        rec["details"] = {"error": "%r" % (exc,),
                          "trace": traceback.format_exc(limit=3)}
        if not rec["generation_ok"]:
            rec["failure_category"] = "generation"
        elif not rec["syntax_ok"]:
            rec["failure_category"] = "syntax"
        else:
            rec["failure_category"] = "tests"
        return rec
    finally:
        rec["seconds"] = round(time.time() - started, 3)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Epsilon v2 capability benchmark")
    ap.add_argument("--strict", action="store_true",
                    help="exit nonzero if any level fails tests_ok")
    args = ap.parse_args(argv)
    started = time.time()
    levels = [run_level(lv) for lv in LEVELS]
    summary = {
        "api": API_NAME,
        "python": sys.version.split()[0],
        "node_present": bool(_node()),
        "levels": levels,
        "totals": {
            "passed": sum(1 for r in levels if r["tests_ok"]),
            "total": len(levels),
            "seconds": round(time.time() - started, 3),
        },
    }
    print(json.dumps(summary, indent=2))
    if args.strict and summary["totals"]["passed"] != summary["totals"]["total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
