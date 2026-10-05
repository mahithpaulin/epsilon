# Epsilon v2 CLI

```
python -m epsilon generate SPEC [--lang python|javascript|typescript]
    [--out DIR] [--no-test] [--max-iterations N] [--timeout S]
    [--verbose] [--nondeterministic]
python -m epsilon single SPEC [--lang LANG]     # legacy one-function output
python -m epsilon validate TARGET [--lang LANG] # file or project dir
python -m epsilon test TARGET [--lang LANG] [--timeout S]
python -m epsilon repair TARGET [--lang LANG] [--max-iterations N]
    [--max-files N]
python -m epsilon inspect TARGET                # project context JSON
python -m epsilon explain TARGET                # plan summary
python -m epsilon bench [--quick] [--json]      # capability benchmark
```

`generate` prints the `ProjectReport` JSON (state, verdicts, tests, repairs,
files, metrics) and writes the project plus `.epsilon-project.json` (HIR)
into `--out`, which `validate/repair/inspect/explain` consume. Exit 0 on
`PASS`/`UNKNOWN`, 1 on `FAIL`. `single` prints code only (v1 behavior).
