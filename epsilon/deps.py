"""Epsilon v2 dependency engine. Stdlib only, deterministic. Never installs."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re

from .errors import DEPS, FAIL, PASS, UNKNOWN, Verdict, err, warn

STDLIB_PY = frozenset([
    "sys", "os", "re", "json", "math", "pathlib", "argparse", "unittest",
    "sqlite3", "http", "urllib", "dataclasses", "typing", "collections",
    "itertools", "functools", "datetime", "time", "random", "string", "csv",
    "io", "shutil", "subprocess", "tempfile", "logging", "copy", "enum",
    "abc", "hashlib", "hmac", "secrets", "uuid", "statistics", "decimal",
    "fractions", "numbers", "html", "xml", "email", "base64", "binascii",
    "struct", "socket", "threading", "queue", "signal", "contextlib",
    "importlib", "inspect", "ast", "dis", "gc", "weakref", "types",
    "traceback", "pdb", "profile", "cProfile", "doctest", "venv", "site",
    "builtins",
])

STDLIB_JS = frozenset([
    "fs", "path", "os", "util", "events", "http", "https", "url",
    "querystring", "crypto", "assert", "stream", "child_process",
    "worker_threads", "readline", "perf_hooks",
])

# Third-party -> stdlib equivalent notes for stdlib-first warnings.
STDLIB_EQUIV = {
    "requests": "urllib.request",
    "httpx": "urllib.request",
    "aiohttp": "urllib.request",
    "pyyaml": "json",
    "yaml": "json",
    "toml": "tomllib",
}

_JS_FROM_RE = re.compile(
    r"(?:import|export)\s+[^'\";]*?\bfrom\s+['\"]([^'\"]+)['\"]"
)
_JS_IMPORT_SIDE_RE = re.compile(r"import\s+['\"]([^'\"]+)['\"]")
_JS_REQUIRE_RE = re.compile(r"require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")
_JS_DYNAMIC_IMPORT_RE = re.compile(r"import\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")

_PY_LANGS = {"python", "py"}
_JS_LANGS = {"javascript", "js", "typescript", "ts"}


def _norm_lang(language) -> str:
    return str(language or "").lower().strip()


def imports_of_file(path, language) -> set[str]:
    """Top-level imports of one file. Empty set on missing/unparseable input."""
    lang = _norm_lang(language)
    # Infer from extension when language is unknown/empty.
    if lang not in _PY_LANGS and lang not in _JS_LANGS:
        ext = os.path.splitext(str(path))[1].lower()
        if ext == ".py":
            lang = "python"
        elif ext in (".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"):
            lang = "javascript"
        else:
            return set()
    try:
        p = str(path)
    except Exception:
        return set()
    if not p or not os.path.isfile(p):
        return set()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except (OSError, ValueError):
        return set()
    if lang in _PY_LANGS:
        try:
            tree = ast.parse(text, filename=p)
        except (SyntaxError, ValueError):
            return set()
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    name = (a.name or "").strip()
                    if not name:
                        continue
                    # relative 'import .x' is invalid; skip dotted-relative.
                    if name.startswith("."):
                        continue
                    top = name.split(".")[0].strip()
                    if top:
                        found.add(top)
            elif isinstance(node, ast.ImportFrom):
                level = getattr(node, "level", 0) or 0
                mod = (node.module or "").strip()
                if level and level > 0:
                    # Relative: preserve signal with leading dot.
                    if mod:
                        top = mod.split(".")[0].strip()
                        if top:
                            found.add("." + top)
                    else:
                        for a in node.names:
                            nm = (a.name or "").strip()
                            if nm and nm != "*":
                                found.add("." + nm.split(".")[0])
                else:
                    if mod:
                        top = mod.split(".")[0].strip()
                        if top:
                            found.add(top)
                    # 'from . import x' with level 0 cannot happen; ignore.
        return found
    # JS/TS regex-lite scan.
    found = set()
    for pat in (_JS_FROM_RE, _JS_IMPORT_SIDE_RE, _JS_REQUIRE_RE, _JS_DYNAMIC_IMPORT_RE):
        try:
            for m in pat.findall(text):
                s = (m or "").strip()
                if s:
                    found.add(s)
        except re.error:
            continue
    return found


def _coerce_str_set(items) -> set[str]:
    out: set[str] = set()
    if items is None:
        return out
    if isinstance(items, str):
        s = items.strip()
        if s:
            out.add(s)
        return out
    try:
        for x in items:
            if isinstance(x, str) and x.strip():
                out.add(x.strip())
    except TypeError:
        return set()
    return out


def _js_package_name(spec: str) -> str:
    core = spec[5:] if spec.startswith("node:") else spec
    if core.startswith("@"):
        parts = core.split("/")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return parts[0] + "/" + parts[1].split("?")[0]
        return core
    return core.split("/")[0].split("?")[0]


def classify_imports(imports, project_modules=None, language="python") -> tuple:
    """Split imports into (stdlib, project_relative, third_party).

    imports: iterable of specifier strings (py top-levels or js raw specifiers).
    project_modules: set/list of local top-level module or package names.
    language: python | javascript | typescript (js/ts aliases accepted).
    Relative imports (leading '.' or '/' for js, leading '.' for py) are
    always project_relative.
    """
    # Robustness: second positional arg may be a language string.
    if isinstance(project_modules, str) and project_modules.lower() in _PY_LANGS | _JS_LANGS:
        language = project_modules
        project_modules = set()
    # Robustness: project passed as dict with known keys.
    if isinstance(project_modules, dict):
        for k in ("project_modules", "modules", "local_modules"):
            if k in project_modules:
                project_modules = project_modules[k]
                break
        else:
            project_modules = set()
    if project_modules is None:
        proj = set()
    elif isinstance(project_modules, (set, list, tuple, frozenset)):
        proj = {str(m).strip() for m in project_modules if isinstance(m, str) and str(m).strip()}
    else:
        proj = _coerce_str_set(project_modules)
    # Also index top-level variants for dotted entries.
    proj_tops = {p.split(".")[0] for p in proj if p}
    proj_all = proj | proj_tops
    lang = _norm_lang(language)
    if lang not in _PY_LANGS and lang not in _JS_LANGS:
        lang = "python"
    stdlib: set[str] = set()
    relative: set[str] = set()
    third: set[str] = set()
    items = _coerce_str_set(imports)
    for raw in sorted(items):
        s = raw.strip()
        if not s:
            continue
        if lang in _JS_LANGS:
            if s.startswith(".") or s.startswith("/"):
                relative.add(s)
                continue
            if s.startswith("node:"):
                core = s[5:].split("/")[0]
                stdlib.add(core if core else s)
                continue
            pkg = _js_package_name(s)
            bare = pkg.strip()
            if not bare:
                continue
            if bare in STDLIB_JS:
                stdlib.add(bare)
            elif bare in proj_all or pkg in proj_all:
                relative.add(pkg)
            else:
                third.add(pkg)
        else:
            if s.startswith("."):
                name = s.lstrip(".").split(".")[0].strip() if s.lstrip(".") else ""
                if name:
                    relative.add(name)
                else:
                    # 'from . import x' recorded as '.x' normally has a name;
                    # bare '.' alone carries no module — keep raw marker.
                    relative.add(s)
                continue
            top = s.split(".")[0].split("/")[0].strip()
            if not top:
                continue
            if top in proj_all:
                relative.add(top)
            elif top in STDLIB_PY:
                stdlib.add(top)
            else:
                third.add(top)
    return (stdlib, relative, third)


def _proj_get(project, *keys, default=None):
    if project is None:
        return default
    if isinstance(project, dict):
        for k in keys:
            if k in project and project[k] is not None:
                return project[k]
        return default
    for k in keys:
        if hasattr(project, k):
            try:
                v = getattr(project, k)
            except Exception:
                continue
            if v is not None:
                return v
    return default


def _normalize_dep_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, dict):
        return sorted([str(k).strip() for k in v.keys() if str(k).strip()])
    try:
        out = [str(x).strip() for x in v if str(x).strip()]
    except TypeError:
        return []
    return sorted(set(out))


def sync_manifests(project) -> tuple:
    """Build manifest files for declared third-party deps only.

    Returns (files: dict[path -> content], warnings: list[str]).
    python -> requirements.txt (pinned-or-loose per version_spec).
    javascript/typescript -> package.json (deps/devDeps).
    stdlib-first policy adds warnings when a dep has a stdlib equivalent.
    """
    lang = _norm_lang(_proj_get(project, "language", "target_language", default="python"))
    if lang not in _PY_LANGS and lang not in _JS_LANGS:
        lang = "python"
    policy = str(_proj_get(project, "dependency_policy", default="stdlib-first") or "stdlib-first")
    raw_third = _proj_get(project, "third_party", "dependencies", "deps", default=[])
    # If dependencies is a dict of dep->spec it doubles as version_spec.
    version_spec = _proj_get(project, "version_spec", "versions", "pins", default={})
    if isinstance(raw_third, dict):
        merged_specs = dict(raw_third)
        if isinstance(version_spec, dict):
            for k, v in version_spec.items():
                merged_specs.setdefault(k, v)
        version_spec = merged_specs
        third = _normalize_dep_list(list(raw_third.keys()))
    else:
        third = _normalize_dep_list(raw_third)
    proj_mods = _normalize_dep_list(_proj_get(project, "project_modules", "modules", default=[]))
    proj_set = set(proj_mods) | {m.split(".")[0] for m in proj_mods}
    # Keep only genuine third-party: drop stdlib and local modules.
    kept: list[str] = []
    for dep in third:
        top = dep.split(".")[0].split("/")[0].strip()
        if lang in _PY_LANGS:
            if top in STDLIB_PY or top in proj_set:
                continue
        else:
            pkg = _js_package_name(dep)
            if pkg in STDLIB_JS or pkg in proj_set or dep.startswith("node:"):
                continue
        kept.append(dep)
    kept = sorted(set(kept))
    warnings: list[str] = []
    if policy == "stdlib-first":
        for dep in kept:
            key = dep.lower()
            # pyyaml import is 'yaml'; cover both spellings.
            if key in STDLIB_EQUIV:
                warnings.append(
                    "third-party '%s' has stdlib equivalent '%s': prefer stdlib under stdlib-first policy"
                    % (dep, STDLIB_EQUIV[key])
                )
    warnings = sorted(warnings)
    if lang in _PY_LANGS:
        if not isinstance(version_spec, dict):
            version_spec = {}
        lines: list[str] = []
        for dep in kept:
            spec = ""
            if dep in version_spec:
                spec = str(version_spec[dep]).strip()
                if spec in ("", "*", "any", "latest"):
                    spec = ""
                elif spec and spec[0] not in ("=", "<", ">", "~", "!", ";", " "):
                    spec = "==" + spec
            lines.append(("%s%s" % (dep, spec)) if spec else dep)
        content = ("\n".join(lines) + "\n") if lines else ""
        return ({"requirements.txt": content}, warnings)
    # JS/TS -> package.json
    pkg_name = str(_proj_get(project, "name", "package_name", default="epsilon-out") or "epsilon-out")
    pkg_version = str(_proj_get(project, "version", default="0.1.0") or "0.1.0")
    if not isinstance(version_spec, dict):
        version_spec = {}
    deps: dict[str, str] = {}
    for dep in kept:
        v = version_spec.get(dep, "*")
        vs = str(v).strip() if v is not None else "*"
        if vs in ("", "any", "latest"):
            vs = "*"
        deps[dep] = vs
    dev_raw = _proj_get(project, "dev_dependencies", "dev_deps", "devDependencies", default=[])
    dev_list = _normalize_dep_list(dev_raw)
    dev_deps: dict[str, str] = {}
    for dep in sorted(set(dev_list)):
        pkg = _js_package_name(dep)
        if pkg in STDLIB_JS or pkg in proj_set or dep.startswith("node:"):
            continue
        if pkg in deps:
            continue
        v = version_spec.get(pkg, version_spec.get(dep, "*"))
        vs = str(v).strip() if v is not None else "*"
        if vs in ("", "any", "latest"):
            vs = "*"
        dev_deps[pkg] = vs
    pkg_obj: dict = {"name": pkg_name, "version": pkg_version, "dependencies": deps}
    if dev_deps:
        pkg_obj["devDependencies"] = dev_deps
    content = json.dumps(pkg_obj, sort_keys=True, indent=2) + "\n"
    return ({"package.json": content}, warnings)


def _coerce_policy(policy) -> str:
    if isinstance(policy, dict):
        for k in ("dependency_policy", "policy"):
            if k in policy and isinstance(policy[k], str):
                return policy[k].strip().lower() or "stdlib-first"
        return "stdlib-first"
    if hasattr(policy, "dependency_policy"):
        try:
            v = getattr(policy, "dependency_policy")
            if isinstance(v, str) and v.strip():
                return v.strip().lower()
        except Exception:
            pass
    if isinstance(policy, str) and policy.strip():
        return policy.strip().lower()
    return "stdlib-first"


def check_availability(third_party, policy="stdlib-first", language="python", out_dir=None) -> Verdict:
    """Check third-party deps are importable. Never installs anything.

    python: importlib.util.find_spec per dep (FAIL when any missing).
    node: always UNKNOWN per package (cannot verify without install).
    policy frozen + any third-party -> FAIL (vendor/eliminate).
    Empty set -> PASS.
    """
    if isinstance(third_party, dict):
        deps = sorted({str(k).strip() for k in third_party.keys() if str(k).strip()})
    elif isinstance(third_party, str):
        deps = [third_party.strip()] if third_party.strip() else []
    else:
        try:
            deps = sorted({str(d).strip() for d in (third_party or []) if str(d).strip()})
        except TypeError:
            deps = []
    pol = _coerce_policy(policy)
    lang = _norm_lang(language)
    if lang not in _PY_LANGS and lang not in _JS_LANGS:
        lang = "python"
    if not deps:
        return Verdict(layer=DEPS, state=PASS, errors=[], details={"available": [], "missing": [], "policy": pol})
    if pol == "frozen":
        return Verdict(
            layer=DEPS,
            state=FAIL,
            errors=[err(DEPS, "frozen policy forbids third-party deps %s; vendor or eliminate them" % (deps,))],
            details={"missing": list(deps), "available": [], "policy": pol},
        )
    if lang in _JS_LANGS:
        details = {
            "unknown": list(deps),
            "policy": pol,
            "note": "cannot verify node packages without install",
        }
        return Verdict(
            layer=DEPS,
            state=UNKNOWN,
            errors=[warn(DEPS, "cannot verify node package '%s' without install" % d) for d in deps],
            details=details,
        )
    available: list[str] = []
    missing: list[str] = []
    for dep in deps:
        top = dep.split(".")[0].split("/")[0].strip()
        candidates = [top, top.replace("-", "_"), top.replace("-", "")]
        # yaml/pip-name alias: pyyaml installs import 'yaml'.
        if top.lower() == "pyyaml":
            candidates.append("yaml")
        found = False
        for cand in dict.fromkeys(candidates):
            if not cand:
                continue
            try:
                spec = importlib.util.find_spec(cand)
            except (ImportError, ValueError, AttributeError):
                continue
            except Exception:
                continue
            if spec is not None:
                found = True
                break
        if found:
            available.append(dep)
        else:
            missing.append(dep)
    if missing:
        return Verdict(
            layer=DEPS,
            state=FAIL,
            errors=[err(DEPS, "missing third-party package '%s': not importable; vendor, eliminate, or allow install" % m) for m in missing],
            details={"available": available, "missing": missing, "policy": pol},
        )
    return Verdict(
        layer=DEPS,
        state=PASS,
        errors=[],
        details={"available": available, "missing": [], "policy": pol},
    )
