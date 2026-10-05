# Epsilon v2 IR (HIR)

Language-neutral, typed, JSON-serializable (`hir.to_dict` /
`hir.node_from_dict`). Extend via `@register_node` — no core edits needed.

- **Project**: `Project{name, description, language, files, dependencies,
  commands, endpoints, cli, models, tests, env, entry, meta}`.
- **Files**: `SourceFile{path, language, doc, imports, declarations,
  main_block}`, `ConfigFile{path, format, data}`, `DocFile`.
- **Declarations**: `FuncDef{name, params, returns, doc, behaviors, body,
  decorators, is_async}`, `ClassDef`, `VarDecl`, `Import`, `DataModel`,
  `ApiEndpoint`, `CliCommand`, `TestSpec{name, target, cases, setup}`,
  `Dependency`, `EnvVar`, `Command`, `TypeRef`, `Param`.
- **Statements**: `Assign IndexAssign AttrAssign AugAssign Return If
  ForIn While Try/Handler Raise Assert ExprStmt With Pass Break Continue`.
  No `ForRange`: loops are `ForIn` over `range()` calls, so negative steps
  keep native semantics in every backend.
- **Expressions**: `Literal Var BinOp Compare BoolOp UnaryOp Call Attr
  Subscript ListLit DictLit SetLit FStr Await_ Starred`.
- **Behaviors** (function-level build hints, backend-agnostic): see
  `snippets.SUPPORTED` (23 kinds). Formula/predicate strings parse through
  `expr.parse_expr` (real tokenizer + Pratt parser); unparsable or unknown
  kinds become explicit `Raise` nodes, never guessed code.
