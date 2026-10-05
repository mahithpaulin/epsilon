# SWE-mini methodology (SWE-bench-inspired, NOT SWE-bench)

Scores here are **not comparable** to the SWE-bench leaderboard. What is
mirrored faithfully, and what is honestly different:

Mirrored: base project verified green before defect injection (base_commit
analog: content hash recorded); ONE injected defect per task (defect
taxonomy: wrong-return, off-by-one, missing-guard, status-code,
reverse-flag, swapped-args, wrong-cond-op, dropped-return, missing-import,
manifest-drift); F2P/P2P split validated like SWE-bench's own task
validation; repair forbidden from touching `tests/` (snapshot-enforced;
contamination = unresolved); binary resolved rule (ALL F2P + ALL P2P, no
partial credit); `resolved/total` + iters + categories reported.

Different (labeled, not hidden): synthetic single injected defects, not real
GitHub issues + human PRs; toy multi-file scale, not 12-repo history; no
Verified-style human filtering; custom harness, no Docker matrix; and one
deliberate deviation — F2P files stay visible during repair (hiding them
left the loop with zero signal, since validation is clean by construction).
The anti-cheat is the contamination guard plus the fact that repair
strategies never read test bodies (only TARGET_PATH metadata for
localization); any test edit at all counts the task unresolved.

One documented orchestrator step: `needs-regeneration` hints re-render the
single file from the pristine HIR plan. Rationale: the defect corrupted
generated text while the plan stayed intact, so restoring from plan is the
minimal correct patch. Plan-level bugs are out of scope (stated, not hidden).
