# Epsilon v2.5-experimental — general symbolic code-generation engine

Epsilon takes a software specification and produces a **complete, runnable,
multi-file project**: implementation, tests, manifests, docs — then validates,
executes, diagnoses, and repairs it. Deterministic, local, stdlib-only
(no LLM, no network, no GPU).

```bash
pip install -e .                    # or just run from the repo root
python -m epsilon generate "Todo CLI with JSON storage" --out ./dist
python -m epsilon generate "Notes REST API" --lang python --out ./notes
python -m epsilon generate "Todo CLI" --lang go --out ./todo-go
python -m epsilon single "function add(a, b) returns sum"   # legacy one-liner
```

## What v2 does

```
spec -> requirements -> plan -> typed IR (HIR) -> bodies -> tests
     -> render (py/js/ts) -> validate -> execute -> repair (bounded) -> report
```

- **Projects, not snippets**: CLI apps, REST APIs, libraries, pipelines,
  games, db-backed tools — composed from roles (domain/interface/store/engine),
  not per-app templates.
- **Layered validation** with explicit states `PASS / FAIL / UNKNOWN /
  UNAVAILABLE` — never claims what it didn't verify (no-node → `UNKNOWN`,
  no-mypy → `UNAVAILABLE`).
- **Repair loop**: classifies failures (missing import, typo, manifest drift,
  syntax), patches minimally, re-validates, bounded by `--max-iterations`.
- **Python + JavaScript/TypeScript** full backends, plus **25 table-driven
  languages** (Go Rust Java C C++ C# Ruby PHP Swift Kotlin Dart Lua Perl R
  Julia Scala Haskell Fortran COBOL Ada MATLAB VB ObjC Groovy Elixir) — one
  generic renderer, per-language tables, honest tiers; see `docs/LANGUAGES.md`.

## Capability (measured, `bench/capability.py`)

10/10 levels: single function, multi-function module, multi-file package, CLI,
API, persistence, external-dep manifest correctness, repair-after-defect,
unfamiliar spec, cross-language py+js. Plus `bench/mbpp_mini.py`: 16/20 exec
(4 stretch fail exec, pass syntax — honest v2 scope),
`bench/bigbench_code.py`: 1/10 solvers on BBH tasks (boundary mapped),
`bench/livecodebench_mini.py`: 0/6 on real contest items (gap analysis),
`bench/swe_mini.py`: SWE-style repair rate (see below),
`bench/lang_matrix.py`: 27/28 render, 0 unexpected failures.

## Docs

- `docs/ARCHITECTURE.md` — subsystems and data flow
- `docs/LANGUAGES.md` — 28-language coverage and tiers
- `docs/PERF.md` — measured optimizations (~2x repeat validation)
- `docs/SWE_MINI.md` — repair benchmark methodology
- `docs/IR.md` — the typed intermediate representation
- `docs/BACKENDS.md` — language backends
- `docs/CLI.md` — command reference
- `docs/DEV.md` — develop, test, benchmark

## Limits (honest)

- SQLite backend is scaffolded (`NotImplementedError`); JSON-file persistence works.
- JS full-project support is best-effort; Python is the strong target.
- Third-party deps are declared/synced, never auto-installed under `restricted`.
- Repair covers imports/typos/manifests/syntax + file regeneration hints;
  deep semantic bugs are reported, not silently "fixed".
