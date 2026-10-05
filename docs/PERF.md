# Epsilon 2.5 performance notes (measured, not claimed)

Measured on-device (Termux, 15-file generated CLI project, 3 validation
passes in one process):

- Repeat passes: **0.42s → 0.20s** (~2x) via the shared parse/text/manifest
  caches. First (cold) pass unchanged — caches help the repair loop, which
  re-validates up to 5 times per build in-process.

What was done (audit items, all landed):

1. **Parse cache** (`validate._cached_tree`): one `ast.parse` per file
   version per process instead of ~7 (syntax-prime, ast ×2, imports,
   symbols ×2, discovery). Text reads share `_TEXT_CACHE`.
2. **Manifest cache** (`_declared_third_party_cached`): manifests parsed
   once per directory version instead of per layer (imports + structure).
3. **Tool lookup cache** (`_tool`): `shutil.which` ×3 per file → ×1 per run.
4. **Stdlib set** (`_stdlib_names`): `lru_cache`, rebuilt never per layer.
5. **Walk prune fix**: `node_modules` and `.cache` pruned separately (the
   old combined entry never matched, so whole subtrees were walked).
6. **Smoke gating** (`pipeline.build`): entry-point smoke runs only on
   otherwise-green builds, never once per failing iteration.
7. **Stage timings**: every build report carries
   `metrics.stage_secs{validate, tests, smoke}` — optimize what you measure.

Escape hatch: `EPSILON_NO_CACHE=1` disables every cache (keys are
path+mtime+size, so repair edits self-invalidate; caches are size-bounded).

Generated-code efficiency is O(n)-by-construction for the loop vocabulary
(accumulate/count/filter/transform/minmax/dedupe); `sorted()` is used where
idiomatic. `bench/capability.py` reports per-level seconds.
