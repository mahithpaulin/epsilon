# Epsilon 2.5 languages (28 total)

3 **full** backends (idiomatic, verified): Python, JavaScript, TypeScript.

25 **table-driven** backends (`lang_tables.py` + generic `gen_table.py`
renderer). One engine, per-language surface tables. Honesty rules: unknown
node kinds raise naming the kind; `gaps` per language documents refused
constructs; verification is tool-conditional (`PASS` only from a real
checker, else `UNKNOWN`, TS-without-tsc `UNAVAILABLE`).

| Language | Tier | Verify here | Notes |
|---|---|---|---|
| Go | general | UNKNOWN (no gofmt) | C-style loops, `:=`, struct+funcs for classes |
| Rust | general | `rustc --crate-type=lib` (timeout→UNKNOWN) | `..` ranges, `(lo..hi).rev()` for step −1, `Option` for null |
| Java | general | UNKNOWN | `.equals` lowering for string `==`; file wrapped in class |
| C | general | UNKNOWN | `strcmp` lowering; struct+funcs; needs `<stdbool.h>`/`<string.h>` for bool/strings |
| C++ | general | UNKNOWN | `std::sort`/`reverse` idioms via lambdas |
| C# | general | UNKNOWN | LINQ lowerings; `.Count` is the common-case choice |
| Ruby | general | `ruby -c` if present | untyped, `end` blocks |
| PHP | general | `php -l` if present | `$` sigils |
| Swift | general | UNKNOWN | `stride(from:to:by:)` for stepped ranges |
| Kotlin | general | UNKNOWN | `until`/`downTo` ranges |
| Dart | general | UNKNOWN | `..sort()` cascade idiom avoided; explicit forms |
| Lua | general | UNKNOWN | 1-based index note; tables for classes |
| Perl | general | `perl -c` | `eq`/`ne` heuristic by literal; `bless` for classes |
| R | general | UNKNOWN | `<-` assignment; S3-list records |
| Julia | general | UNKNOWN | `lo:step:hi` ranges; structs |
| Scala | general | UNKNOWN | `<-` comprehensions |
| Haskell | general | UNKNOWN (expression-only) | refuses statement bodies (documented, not a bug) |
| Fortran | legacy | UNKNOWN | `do i=lo,hi` loops; derived types; no exceptions |
| COBOL | legacy | UNKNOWN | subset only: vars/IF/PERFORM/DISPLAY/COMPUTE; file shell emitted |
| Ada | legacy | UNKNOWN | `..` ranges; tagged types; `raise..with` |
| MATLAB | legacy | UNKNOWN | 1-based index note; `classdef` for classes |
| Visual Basic | legacy | UNKNOWN | `End If/While/For` closers |
| Objective-C | general | UNKNOWN | C-subset + `NSLog`; message sends refused |
| Groovy | general | UNKNOWN | dynamic `def`; both loop styles |
| Elixir | general | UNKNOWN | implicit return; `for..do`; no `while`/mutation |

`bench/lang_matrix.py` renders a canonical sample in all 28 and reports
`render_ok` + verifier state per language. `python -m epsilon generate SPEC
--lang go --out dir` works for any listed language.
