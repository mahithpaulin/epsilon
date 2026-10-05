# Epsilon v2 architecture

## Pipeline (`epsilon/pipeline.py::build`)

1. **Requirements** (`requirements.py::analyze`) — spec text → `Requirements`:
   goals, constraints, inputs/outputs, interfaces (cli/api/store/frontend…),
   persistence, entities (case-preserved), capability behaviors
   `{verb, object, detail}`, NFRs, ambiguities, assumptions. Generic verb
   lexicon; no algorithm recognizers.
2. **Planner** (`planner.py`) — `detect_type` scores 11 project types over
   signal counts (≥2 distinct signals or generic-library fallback, documented
   priority ties). `plan_project` composes role planners: domain (models +
   behavior-clustered functions), cli (argparse + `CliCommand`s), api
   (endpoints + `(status, body)` handlers), store (json/sqlite stdlib),
   engine (pure step + run loop), tests (behavioral cases), configs, docs.
3. **Normalize** — extension remap per language, per-language import filtering,
   models attached as `@dataclass` classes.
4. **Materialize** — planner behavior names + signatures → full snippet dicts
   (`enrich_behaviors`) → `snippets.build_body` → HIR statements; CRUD verbs
   get store-backed tails; CLI dispatcher, API serve loop, JSON store IO built
   as raw HIR. `finalize_tests` regenerates cases from the same semantics
   table that built the bodies (single source of truth).
5. **Render** — backend registry (`backends.py`) → `gen_python` / `gen_js`;
   `testgen` renders `TestSpec`s to unittest / node:test files.
6. **Validate** (`validate.py`) — layers: syntax, ast, imports, symbols
   (lexical `ScopeBuilder`, per-file scopes + cross-file import resolution),
   structure, types (mypy or `UNAVAILABLE`), tests-discovery.
7. **Execute** (`sandbox.py`) — scrubbed-env subprocesses, timeouts, capped
   output; `run_tests` parses real runner output, never claims passes.
8. **Repair** (`repair.py`) — ordered strategies, one minimal edit per
   attempt, every attempt recorded; loop re-validates until green or budget
   (`repair_iterations`, `max_repair_files`).
9. **Report** — `ProjectReport{state, verdicts, tests, repairs, files, metrics}`.

## Support modules

- `errors.py` — `EpsilonError{category, severity, message, file, line, col,
  symbol, probable_cause, suggested_repair}`; states `PASS/FAIL/UNKNOWN/
  UNAVAILABLE`.
- `config.py` — `EpsilonConfig` (language, out dir, validation level, tests,
  repair budget, dependency/sandbox policy, verbosity, determinism, timeouts).
- `events.py` — structured stage log, silent unless `--verbose`.
- `hir.py` — typed IR nodes + `NODE_REGISTRY` (`register_node` extends
  without touching core) + dict round-trip.
- `expr.py` — tokenizer + Pratt parser for formula text → HIR expressions.
- `snippets.py` — 23 generic behavior constructors (validate, compute,
  transform/filter/accumulate/count, none-where, search, sort, dedupe,
  group-count, reverse, average, minmax-loop, branch-return, repeat-range,
  recurse, raise…). Unknown kinds → `NotImplementedError`, never fake code.
- `deps.py` — stdlib sets, import classification, manifest sync,
  availability checks (never installs under `restricted`).
- `context.py` — project knowledge model (symbols, imports, apis, errors,
  repairs) built from generated sources.
- `compat_v1.py` — quarantined legacy single-function adapter (16 patterns);
  not core architecture. The fake v1 "ranker" was removed and replaced with
  a documented real-signal score (single candidate, no ranking claims).

## Fixed v1 weaknesses

Lexical scopes (no global namespace merge); case-preserving identifiers;
negative-step loops correct in both backends (`__range` helper in JS);
JS-without-node → `UNKNOWN`; verdicts strictly the 4 states; HIR `elifs`
and registry round-trip through JSON.
