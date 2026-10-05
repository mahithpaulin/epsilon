"""Epsilon v2 behavior-constructor layer. Composes HIR statements from behavior dicts."""
from __future__ import annotations

from . import hir as H
from .expr import parse_expr, ExprError

SUPPORTED: set[str] = {
    "validate-inputs",
    "guard-raise",
    "compute",
    "return-expr",
    "transform-each",
    "filter-where",
    "accumulate",
    "count-where",
    "none-where",
    "search-first",
    "sort-by",
    "dedupe",
    "group-count",
    "reverse-seq",
    "average",
    "minmax-loop",
    "branch-return",
    "build-list",
    "append",
    "init-list",
    "repeat-range",
    "recurse",
    "raise",
}


def _lit(value) -> object:
    return H.Literal(value=value)


def _msg_node(msg) -> object | None:
    if msg is None:
        return None
    return H.Literal(value=msg)


def _parse_formula(text) -> object:
    if isinstance(text, str):
        return parse_expr(text)
    if hasattr(text, "__dataclass_fields__"):
        return text
    if isinstance(text, dict) and "kind" in text:
        return H.node_from_dict(text)
    return H.Literal(value=text)


def _bad_formula(text, err) -> object:
    return H.Raise(exc="ValueError", message=H.Literal(value="bad formula %r: %s" % (text, err)))


def _append_stmt(list_name: str, value) -> object:
    return H.ExprStmt(
        expr=H.Call(func=H.Attr(obj=H.Var(name=list_name), attr="append"), args=[value], kwargs={})
    )


def _set_add_stmt(set_name: str, value) -> object:
    return H.ExprStmt(
        expr=H.Call(func=H.Attr(obj=H.Var(name=set_name), attr="add"), args=[value], kwargs={})
    )


def build_body(behaviors: list[dict], func_name: str, params: list[str]) -> list:
    try:
        if behaviors is None:
            return [H.Raise(exc="ValueError", message=H.Literal(value="bad formula None: no behaviors"))]
        if not isinstance(behaviors, list):
            return [H.Raise(exc="ValueError", message=H.Literal(value="bad formula %r: behaviors must be a list" % (behaviors,)))]
        out: list = []
        for b in behaviors:
            out.extend(_build_one(b, func_name, params))
        return out
    except (ExprError, ValueError) as e:
        return [_bad_formula("", e)]
    except Exception as e:
        return [H.Raise(exc="ValueError", message=H.Literal(value="bad behavior: %s" % (e,)))]


def _build_one(b: dict, func_name: str, params: list[str]) -> list:
    try:
        if not isinstance(b, dict):
            return [H.Raise(exc="ValueError", message=H.Literal(value="bad behavior %r: must be a dict" % (b,)))]
        kind = b.get("kind", "") or b.get("behavior", "")
        if kind not in SUPPORTED:
            return [H.Raise(exc="NotImplementedError", message=H.Literal(value="unsupported behavior %s" % (kind,)))]
        if kind == "validate-inputs":
            return _b_validate_inputs(b)
        if kind == "guard-raise":
            return _b_guard_raise(b)
        if kind == "compute":
            return _b_compute(b)
        if kind == "return-expr":
            return _b_return_expr(b)
        if kind == "transform-each":
            return _b_transform_each(b)
        if kind == "filter-where":
            return _b_filter_where(b)
        if kind == "accumulate":
            return _b_accumulate(b)
        if kind == "count-where":
            return _b_count_where(b)
        if kind == "none-where":
            return _b_none_where(b)
        if kind == "search-first":
            return _b_search_first(b)
        if kind == "sort-by":
            return _b_sort_by(b)
        if kind == "dedupe":
            return _b_dedupe(b)
        if kind == "group-count":
            return _b_group_count(b)
        if kind == "reverse-seq":
            return _b_reverse_seq(b)
        if kind == "average":
            return _b_average(b)
        if kind == "minmax-loop":
            return _b_minmax_loop(b)
        if kind == "branch-return":
            return _b_branch_return(b)
        if kind == "build-list":
            return _b_build_list(b)
        if kind == "append":
            return _b_append(b)
        if kind == "init-list":
            return _b_init_list(b)
        if kind == "repeat-range":
            return _b_repeat_range(b, func_name, params)
        if kind == "recurse":
            return _b_recurse(b, func_name)
        if kind == "raise":
            return _b_raise(b)
        return [H.Raise(exc="NotImplementedError", message=H.Literal(value="unsupported behavior %s" % (kind,)))]
    except (ExprError, ValueError) as e:
        text = ""
        try:
            if isinstance(b, dict):
                text = str(b.get("kind", ""))
        except Exception:
            text = ""
        return [H.Raise(exc="ValueError", message=H.Literal(value="bad formula in %s: %s" % (text, e)))]
    except Exception as e:
        return [H.Raise(exc="ValueError", message=H.Literal(value="bad behavior: %s" % (e,)))]


def _b_validate_inputs(b: dict) -> list:
    checks = b.get("checks", [])
    if checks is None:
        checks = []
    stmts: list = []
    for ch in checks:
        param = ch.get("param", "")
        pred = ch.get("pred", "")
        exc = ch.get("exc", "ValueError") or "ValueError"
        msg = ch.get("msg", "invalid %s" % (param,) if param else "invalid input")
        cond = _parse_formula(pred)
        stmts.append(
            H.If(
                cond=H.UnaryOp(op="not", operand=cond),
                then=[H.Raise(exc=str(exc), message=_msg_node(msg))],
                elifs=[],
                else_body=[],
            )
        )
    return stmts


def _b_guard_raise(b: dict) -> list:
    pred = b.get("pred", "")
    exc = b.get("exc", "Exception") or "Exception"
    msg = b.get("msg", "")
    cond = _parse_formula(pred)
    return [
        H.If(
            cond=cond,
            then=[H.Raise(exc=str(exc), message=_msg_node(msg))],
            elifs=[],
            else_body=[],
        )
    ]


def _b_compute(b: dict) -> list:
    target = b["target"]
    cond = _parse_formula(b["expr"])
    return [H.Assign(target=str(target), value=cond)]


def _b_return_expr(b: dict) -> list:
    return [H.Return(value=_parse_formula(b["expr"]))]


def _b_transform_each(b: dict) -> list:
    source = str(b["source"])
    item = str(b["item"])
    target = str(b["target"])
    val = _parse_formula(b["expr"])
    return [
        H.Assign(target=target, value=H.ListLit(items=[])),
        H.ForIn(var=item, iter=H.Var(name=source), body=[_append_stmt(target, val)]),
    ]


def _b_filter_where(b: dict) -> list:
    source = str(b["source"])
    item = str(b["item"])
    target = str(b["target"])
    cond = _parse_formula(b["pred"])
    return [
        H.Assign(target=target, value=H.ListLit(items=[])),
        H.ForIn(
            var=item,
            iter=H.Var(name=source),
            body=[
                H.If(
                    cond=cond,
                    then=[_append_stmt(target, H.Var(name=item))],
                    elifs=[],
                    else_body=[],
                )
            ],
        ),
    ]


def _b_accumulate(b: dict) -> list:
    source = str(b["source"])
    item = str(b["item"])
    target = str(b["target"])
    init = _parse_formula(b["init"])
    step = _parse_formula(b["expr"])
    return [
        H.Assign(target=target, value=init),
        H.ForIn(var=item, iter=H.Var(name=source), body=[H.AugAssign(target=target, op="+=", value=step)]),
    ]


def _b_count_where(b: dict) -> list:
    source = str(b["source"])
    item = str(b["item"])
    target = str(b["target"])
    cond = _parse_formula(b["pred"])
    return [
        H.Assign(target=target, value=H.Literal(value=0)),
        H.ForIn(
            var=item,
            iter=H.Var(name=source),
            body=[
                H.If(
                    cond=cond,
                    then=[H.AugAssign(target=target, op="+=", value=H.Literal(value=1))],
                    elifs=[],
                    else_body=[],
                )
            ],
        ),
    ]


def _b_none_where(b: dict) -> list:
    source = str(b["source"])
    item = str(b["item"])
    target = str(b["target"])
    cond = _parse_formula(b["pred"])
    return [
        H.Assign(target=target, value=H.Literal(value=True)),
        H.ForIn(
            var=item,
            iter=H.Var(name=source),
            body=[
                H.If(
                    cond=cond,
                    then=[H.Assign(target=target, value=H.Literal(value=False)), H.Break()],
                    elifs=[],
                    else_body=[],
                )
            ],
        ),
    ]


def _b_search_first(b: dict) -> list:
    source = str(b["source"])
    item = str(b["item"])
    target = str(b["target"])
    cond = _parse_formula(b["pred"])
    miss = b.get("miss", "none")
    stmts: list = [
        H.Assign(target=target, value=H.Literal(value=None)),
        H.ForIn(
            var=item,
            iter=H.Var(name=source),
            body=[
                H.If(
                    cond=cond,
                    then=[H.Assign(target=target, value=H.Var(name=item)), H.Break()],
                    elifs=[],
                    else_body=[],
                )
            ],
        ),
    ]
    if miss == "none" or miss is None:
        return stmts
    if isinstance(miss, str) and miss.startswith("raise:"):
        rest = miss[len("raise:"):]
        if ":" in rest:
            exc, msg = rest.split(":", 1)
            exc = exc.strip() or "Exception"
        else:
            exc = rest.strip() or "Exception"
            msg = ""
        stmts.append(
            H.If(
                cond=H.Compare(op="==", left=H.Var(name=target), right=H.Literal(value=None)),
                then=[H.Raise(exc=str(exc), message=H.Literal(value=msg))],
                elifs=[],
                else_body=[],
            )
        )
        return stmts
    return [H.Raise(exc="ValueError", message=H.Literal(value="bad miss %r" % (miss,)))]


def _b_sort_by(b: dict) -> list:
    source = str(b["source"])
    target = str(b["target"])
    key = b.get("key", None)
    reverse = b.get("reverse", False)
    if isinstance(reverse, str):
        reverse = reverse.strip().lower() in ("true", "1", "yes", "t")
    kwargs: dict = {}
    if key is not None:
        kwargs["key"] = _parse_formula(key)
    kwargs["reverse"] = H.Literal(value=bool(reverse))
    return [
        H.Assign(
            target=target,
            value=H.Call(func=H.Var(name="sorted"), args=[H.Var(name=source)], kwargs=kwargs),
        )
    ]


def _b_dedupe(b: dict) -> list:
    source = str(b["source"])
    target = str(b["target"])
    seen = "_%s_seen" % (target,)
    item = "_%s_item" % (target,)
    return [
        H.Assign(target=target, value=H.ListLit(items=[])),
        H.Assign(target=seen, value=H.SetLit(items=[])),
        H.ForIn(
            var=item,
            iter=H.Var(name=source),
            body=[
                H.If(
                    cond=H.Compare(op="not-in", left=H.Var(name=item), right=H.Var(name=seen)),
                    then=[_set_add_stmt(seen, H.Var(name=item)), _append_stmt(target, H.Var(name=item))],
                    elifs=[],
                    else_body=[],
                )
            ],
        ),
    ]


def _b_group_count(b: dict) -> list:
    source = str(b["source"])
    item = str(b["item"])
    target = str(b["target"])
    key_text = b["key"]
    key_a = _parse_formula(key_text)
    key_b = _parse_formula(key_text)
    key_c = _parse_formula(key_text)
    key_d = _parse_formula(key_text)
    tgt = H.Var(name=target)
    tgt2 = H.Var(name=target)
    tgt3 = H.Var(name=target)
    return [
        H.Assign(target=target, value=H.DictLit(pairs=[])),
        H.ForIn(
            var=item,
            iter=H.Var(name=source),
            body=[
                H.If(
                    cond=H.Compare(op="in", left=key_a, right=tgt),
                    then=[
                        H.IndexAssign(
                            obj=tgt2,
                            index=key_b,
                            value=H.BinOp(
                                op="+",
                                left=H.Subscript(obj=tgt3, index=key_c),
                                right=H.Literal(value=1),
                            ),
                        )
                    ],
                    elifs=[],
                    else_body=[
                        H.IndexAssign(obj=H.Var(name=target), index=key_d, value=H.Literal(value=1))
                    ],
                )
            ],
        ),
    ]


def _b_reverse_seq(b: dict) -> list:
    source = str(b["source"])
    target = str(b["target"])
    return [
        H.Assign(
            target=target,
            value=H.Call(func=H.Var(name="reversed"), args=[H.Var(name=source)], kwargs={}),
        )
    ]


def _b_average(b: dict) -> list:
    source = str(b["source"])
    target = str(b["target"])
    return [
        H.If(
            cond=H.Compare(
                op="==",
                left=H.Call(func=H.Var(name="len"), args=[H.Var(name=source)], kwargs={}),
                right=H.Literal(value=0),
            ),
            then=[H.Raise(exc="ValueError", message=H.Literal(value="empty sequence"))],
            elifs=[],
            else_body=[],
        ),
        H.Assign(
            target=target,
            value=H.BinOp(
                op="/",
                left=H.Call(func=H.Var(name="sum"), args=[H.Var(name=source)], kwargs={}),
                right=H.Call(func=H.Var(name="len"), args=[H.Var(name=source)], kwargs={}),
            ),
        ),
    ]


def _b_minmax_loop(b: dict) -> list:
    source = str(b["source"])
    item = str(b["item"])
    target = str(b["target"])
    mode = str(b.get("mode", "max")).lower()
    if mode == "min":
        op = "<"
    elif mode == "max":
        op = ">"
    else:
        raise ValueError("bad mode %r: expected min|max" % (b.get("mode"),))
    return [
        H.Assign(target=target, value=H.Subscript(obj=H.Var(name=source), index=H.Literal(value=0))),
        H.ForIn(
            var=item,
            iter=H.Var(name=source),
            body=[
                H.If(
                    cond=H.Compare(op=op, left=H.Var(name=item), right=H.Var(name=target)),
                    then=[H.Assign(target=target, value=H.Var(name=item))],
                    elifs=[],
                    else_body=[],
                )
            ],
        ),
    ]


def _b_branch_return(b: dict) -> list:
    branches = b.get("branches", [])
    has_default = "default" in b and b.get("default") is not None
    default = b.get("default", None)
    if not branches:
        if not has_default:
            return []
        return [H.Return(value=_parse_formula(default))]
    first = branches[0]
    first_cond = _parse_formula(first.get("pred", ""))
    first_val = _parse_formula(first.get("value", ""))
    elifs: list = []
    for br in branches[1:]:
        c = _parse_formula(br.get("pred", ""))
        v = _parse_formula(br.get("value", ""))
        elifs.append((c, [H.Return(value=v)]))
    else_body: list = []
    if has_default:
        else_body = [H.Return(value=_parse_formula(default))]
    return [
        H.If(cond=first_cond, then=[H.Return(value=first_val)], elifs=elifs, else_body=else_body)
    ]


def _b_build_list(b: dict) -> list:
    target = str(b["target"])
    items = b.get("items", [])
    return [H.Assign(target=target, value=H.ListLit(items=[_parse_formula(t) for t in items]))]


def _b_append(b: dict) -> list:
    lst = str(b["list"])
    val = _parse_formula(b["expr"])
    return [_append_stmt(lst, val)]


def _b_init_list(b: dict) -> list:
    target = str(b["target"])
    return [H.Assign(target=target, value=H.ListLit(items=[]))]


def _b_repeat_range(b: dict, func_name: str, params: list[str]) -> list:
    var = str(b["var"])
    start = _parse_formula(b["from"])
    stop = _parse_formula(b["to"])
    inner = b.get("body", [])
    nested = build_body(inner, func_name, params)
    return [
        H.ForIn(
            var=var,
            iter=H.Call(func=H.Var(name="range"), args=[start, stop], kwargs={}),
            body=nested,
        )
    ]


def _b_recurse(b: dict, func_name: str) -> list:
    func = str(b.get("func", func_name) or func_name)
    base = b.get("base", [])
    combine_text = b.get("combine", None)
    if combine_text is None:
        raise ValueError("recurse missing combine")
    if not base:
        return [H.Return(value=_parse_formula(combine_text))]
    _ = func
    first = base[0]
    first_cond = _parse_formula(first.get("pred", ""))
    first_val = _parse_formula(first.get("value", ""))
    elifs: list = []
    for ent in base[1:]:
        c = _parse_formula(ent.get("pred", ""))
        v = _parse_formula(ent.get("value", ""))
        elifs.append((c, [H.Return(value=v)]))
    return [
        H.If(
            cond=first_cond,
            then=[H.Return(value=first_val)],
            elifs=elifs,
            else_body=[H.Return(value=_parse_formula(combine_text))],
        )
    ]


def _b_raise(b: dict) -> list:
    exc = str(b.get("exc", "Exception") or "Exception")
    msg = b.get("msg", "")
    return [H.Raise(exc=exc, message=_msg_node(msg))]
