# Epsilon v2 backends

`backends.get_backend(lang)` → `PythonBackend` / `JsBackend(mode)`.
`render_project(project) -> {path: content}`. To add a language: implement the
same 4 methods (`render_project`, `render_file`, `render_decl`,
`render_stmt`/`render_expr`) and `register(name, backend)`.

- **Python** (`gen_python.py`): typed defs, dataclasses for models,
  docstrings, `__main__` guards, minimal hand-rolled TOML/INI writers.
  `reversed(x)` lowers to `x[::-1]` (type-preserving); identifiers verbatim.
- **JS/TS** (`gen_js.py`): CommonJS (`require`/`module.exports`, matching what
  generated tests import), `const`/`let` by reassignment scan, `===`/`!==`,
  `.includes`/`.has` by container shape, template literals, numeric-sort
  lowering for `sorted`, `String()`/`reduce` lowerings, runtime-safe
  `__range` helper (negative steps correct), `Math.trunc` for `//`.
  TS mode adds annotations; JS mode emits JSDoc. `set`/`dict` mutate via
  methods (no rebinding, so no `global` problem exists).
- Both raise `ValueError` naming unknown node kinds instead of guessing.
