"""Epsilon v2 typed intermediate representation.

Language-neutral project model: Project -> files -> declarations ->
statements -> expressions, plus requirements-facing nodes (models, endpoints,
commands, tests, deps, docs). Extensible via register_node(): new node kinds
plug in without touching this module. All nodes JSON-serializable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass

NODE_REGISTRY: dict[str, type] = {}


def register_node(cls: type) -> type:
    NODE_REGISTRY[cls.__name__] = cls
    return cls


def node_from_dict(d: dict):
    cls = NODE_REGISTRY.get(d.get('kind', ''))
    if cls is None:
        raise ValueError('unknown node kind %r' % d.get('kind'))
    kwargs: dict = {}
    for f in fields(cls):
        if f.name == 'kind' or f.name not in d:
            continue
        kwargs[f.name] = _from_plain(d[f.name])
    return cls(**kwargs)


def _from_plain(v):
    if isinstance(v, dict) and 'kind' in v and v.get('kind') in NODE_REGISTRY:
        return node_from_dict(v)
    if isinstance(v, list):
        return [_from_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _from_plain(x) for k, x in v.items()}
    return v


def to_dict(node) -> dict:
    if is_dataclass(node):
        d: dict = {'kind': type(node).__name__}
        for f in fields(node):
            if f.name == 'kind':
                continue
            v = getattr(node, f.name)
            d[f.name] = _to_plain(v)
        return d
    raise TypeError('not a node: %r' % (node,))


def _to_plain(v):
    if is_dataclass(v):
        return to_dict(v)
    if isinstance(v, list):
        return [_to_plain(x) for x in v]
    if isinstance(v, tuple):
        return [_to_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_plain(x) for k, x in v.items()}
    return v


# ---- shared atoms ----

@register_node
@dataclass
class TypeRef:
    name: str
    args: list = field(default_factory=list)
    optional: bool = False


@register_node
@dataclass
class Param:
    name: str
    type: TypeRef | None = None
    default: str | None = None   # source literal, e.g. '0', 'None', '"x"'
    help: str = ''


@register_node
@dataclass
class VarDecl:
    name: str
    type: TypeRef | None = None
    value: str | None = None     # source literal
    mutable: bool = True
    help: str = ''


@register_node
@dataclass
class EnvVar:
    name: str
    default: str = ''
    required: bool = False
    help: str = ''


@register_node
@dataclass
class Import:
    module: str
    names: list[str] = field(default_factory=list)  # empty = plain import
    alias: str | None = None
    third_party: bool = False


@register_node
@dataclass
class Dependency:
    name: str
    version_spec: str = ''
    dev: bool = False


# ---- expressions ----

@register_node
@dataclass
class Literal:
    value: object = None


@register_node
@dataclass
class Var:
    name: str


@register_node
@dataclass
class BinOp:
    op: str
    left: object = None
    right: object = None


@register_node
@dataclass
class Compare:
    op: str
    left: object = None
    right: object = None


@register_node
@dataclass
class BoolOp:
    op: str = 'and'
    values: list = field(default_factory=list)


@register_node
@dataclass
class UnaryOp:
    op: str = 'not'
    operand: object = None


@register_node
@dataclass
class Call:
    func: object = None
    args: list = field(default_factory=list)
    kwargs: dict = field(default_factory=dict)


@register_node
@dataclass
class Attr:
    obj: object = None
    attr: str = ''


@register_node
@dataclass
class Subscript:
    obj: object = None
    index: object = None


@register_node
@dataclass
class ListLit:
    items: list = field(default_factory=list)


@register_node
@dataclass
class DictLit:
    pairs: list = field(default_factory=list)  # list of (key, value) exprs


@register_node
@dataclass
class SetLit:
    items: list = field(default_factory=list)


@register_node
@dataclass
class FStr:
    parts: list = field(default_factory=list)  # str | Expr


@register_node
@dataclass
class Await_:
    value: object = None


@register_node
@dataclass
class Starred:
    value: object = None


# ---- statements ----

@register_node
@dataclass
class Assign:
    target: str = ''
    value: object = None
    annotation: TypeRef | None = None


@register_node
@dataclass
class IndexAssign:
    obj: object = None
    index: object = None
    value: object = None


@register_node
@dataclass
class AttrAssign:
    obj: object = None
    attr: str = ''
    value: object = None


@register_node
@dataclass
class AugAssign:
    target: str = ''
    op: str = '+='
    value: object = None


@register_node
@dataclass
class Return:
    value: object = None


@register_node
@dataclass
class If:
    cond: object = None
    then: list = field(default_factory=list)
    elifs: list = field(default_factory=list)   # list of (cond, body)
    else_body: list = field(default_factory=list)


@register_node
@dataclass
class ForIn:
    var: str = ''
    iter: object = None
    body: list = field(default_factory=list)


@register_node
@dataclass
class While:
    cond: object = None
    body: list = field(default_factory=list)


@register_node
@dataclass
class Handler:
    exc: str | None = None
    name: str | None = None
    body: list = field(default_factory=list)


@register_node
@dataclass
class Try:
    body: list = field(default_factory=list)
    handlers: list = field(default_factory=list)
    else_body: list = field(default_factory=list)
    finally_body: list = field(default_factory=list)


@register_node
@dataclass
class Raise:
    exc: str = 'Exception'
    message: object = None


@register_node
@dataclass
class Assert:
    cond: object = None
    message: object = None


@register_node
@dataclass
class ExprStmt:
    expr: object = None


@register_node
@dataclass
class With:
    items: list = field(default_factory=list)   # list of (expr, name|None)
    body: list = field(default_factory=list)


@register_node
@dataclass
class Pass:
    pass


@register_node
@dataclass
class Break:
    pass


@register_node
@dataclass
class Continue:
    pass


# ---- declarations ----

@register_node
@dataclass
class FuncDef:
    name: str = ''
    params: list = field(default_factory=list)   # list[Param]
    returns: TypeRef | None = None
    doc: str = ''
    behaviors: list = field(default_factory=list)  # list[dict], see snippets
    body: list = field(default_factory=list)       # list[Stmt]
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False


@register_node
@dataclass
class ClassDef:
    name: str = ''
    bases: list[str] = field(default_factory=list)
    doc: str = ''
    fields: list = field(default_factory=list)    # list[VarDecl]
    methods: list = field(default_factory=list)   # list[FuncDef]
    decorators: list[str] = field(default_factory=list)


# ---- files & project ----

@register_node
@dataclass
class SourceFile:
    path: str = ''
    language: str = 'python'
    doc: str = ''
    imports: list = field(default_factory=list)       # list[Import]
    declarations: list = field(default_factory=list)  # ClassDef|FuncDef|VarDecl
    main_block: list = field(default_factory=list)    # list[Stmt] under __main__


@register_node
@dataclass
class ConfigFile:
    path: str = ''
    format: str = 'txt'     # ini | json | toml | txt | cfg
    data: object = None     # dict | str
    comment: str = ''


@register_node
@dataclass
class DocFile:
    path: str = ''
    title: str = ''
    sections: list = field(default_factory=list)  # list of (heading, body)


@register_node
@dataclass
class DataModel:
    name: str = ''
    fields: list = field(default_factory=list)  # list[VarDecl]
    doc: str = ''


@register_node
@dataclass
class ApiEndpoint:
    method: str = 'GET'
    path: str = '/'
    handler: str = ''
    request: str | None = None
    response: str | None = None
    doc: str = ''


@register_node
@dataclass
class CliCommand:
    name: str = ''
    help: str = ''
    args: list = field(default_factory=list)  # list[Param]
    handler: str = ''


@register_node
@dataclass
class TestCase:
    given: object = None       # args: tuple-list | kwargs-dict | single value
    expect: str = ''           # source literal of expected value
    raises: str | None = None  # exception name
    note: str = ''


@register_node
@dataclass
class TestSpec:
    name: str = ''
    target: str = ''           # dotted path, e.g. 'calc.ops.add' or module
    cases: list = field(default_factory=list)
    setup: str = ''           # source snippet run before cases


@register_node
@dataclass
class Command:
    name: str = ''
    argv: list[str] = field(default_factory=list)
    description: str = ''


@register_node
@dataclass
class Project:
    name: str = 'app'
    description: str = ''
    language: str = 'python'
    files: list = field(default_factory=list)  # SourceFile|ConfigFile|DocFile
    dependencies: list = field(default_factory=list)
    commands: list = field(default_factory=list)
    endpoints: list = field(default_factory=list)
    cli: list = field(default_factory=list)    # list[CliCommand]
    models: list = field(default_factory=list)
    tests: list = field(default_factory=list)  # list[TestSpec]
    env: list = field(default_factory=list)
    entry: str = ''            # main runnable path
    meta: dict = field(default_factory=dict)

    def source_files(self) -> list:
        return [f for f in self.files if isinstance(f, SourceFile)]

    def file_paths(self) -> list[str]:
        return [f.path for f in self.files]
