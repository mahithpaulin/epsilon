"""Epsilon requirements analysis: deterministic NLP-lite over a spec string.

Stdlib only. No global mutable state: every helper builds its working
data in locals; the module holds no mutable globals. All ordering is
first-seen (insertion) order or fixed local order, so results are
deterministic for a given input string.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Requirements:
    title: str
    goals: list[str]
    constraints: list[str]
    inputs: list[dict]
    outputs: list[dict]
    interfaces: list[dict]
    dependencies: list[str]
    runtime: str
    persistence: str | None
    data_entities: list[dict]
    behaviors: list[dict]
    non_functional: list[str]
    ambiguities: list[str]
    assumptions: list[str]
    raw: str

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "goals": list(self.goals),
            "constraints": list(self.constraints),
            "inputs": [dict(d) for d in self.inputs],
            "outputs": [dict(d) for d in self.outputs],
            "interfaces": [dict(d) for d in self.interfaces],
            "dependencies": list(self.dependencies),
            "runtime": self.runtime,
            "persistence": self.persistence,
            "data_entities": [
                {"name": e.get("name"), "fields": list(e.get("fields", [])),
                 "desc": e.get("desc", "")}
                for e in self.data_entities
            ],
            "behaviors": [dict(b) for b in self.behaviors],
            "non_functional": list(self.non_functional),
            "ambiguities": list(self.ambiguities),
            "assumptions": list(self.assumptions),
            "raw": self.raw,
        }


def _split_clauses(text: str) -> list[str]:
    """Split on . ; newlines, then on and/then/plus/also/with. Max 20."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"[.;\n]+", normalized)
    clauses: list[str] = []
    for part in parts:
        subs = re.split(
            r"\b(?:and|then|plus|also|with)\b", part, flags=re.IGNORECASE
        )
        for sub in subs:
            t = sub.strip()
            if t:
                clauses.append(t)
                if len(clauses) >= 20:
                    return clauses
    return clauses[:20]


def _is_substantive(clause: str) -> bool:
    t = clause.strip()
    if len(t) < 4:
        return False
    for ch in t:
        if ch.isalnum():
            return True
    return False


def _extract_entities(text: str) -> list[str]:
    """Case-preserving entity harvest in first-seen order.

    Only lowercased copies are used as dedupe keys; stored forms keep
    the original casing exactly as written.
    """
    stop = (
        "the", "a", "an", "and", "or", "but", "with", "plus", "also",
        "then", "than", "for", "from", "that", "this", "these", "those",
        "it", "its", "they", "them", "when", "where", "which", "who",
        "whom", "whose", "how", "what", "why", "not", "no", "yes",
        "all", "any", "each", "every", "some", "such", "only", "just",
        "over", "under", "between", "into", "onto", "upon", "about",
        "above", "below", "after", "before", "during", "while", "since",
        "until", "unless", "although", "because", "therefore", "however",
        "moreover", "furthermore", "hence", "thus", "http", "rest",
        "api", "cli", "sql",
    )
    cands: list[tuple[int, str]] = []

    def _add(pos: int, name: str) -> None:
        n = name.strip()
        if n:
            cands.append((pos, n))

    for m in re.finditer(r'"([^"\n]{1,60})"', text):
        _add(m.start(), m.group(1))
    for m in re.finditer(r"'([^'\n]{1,60})'", text):
        _add(m.start(), m.group(1))
    for m in re.finditer(r"`([^`\n]{1,60})`", text):
        _add(m.start(), m.group(1))
    for m in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*_[A-Za-z0-9_]+\b", text):
        tok = m.group(0)
        if len(tok) <= 60:
            _add(m.start(), tok)
    for m in re.finditer(
        r"\b(?:list|dictionary)\s+of\s+([A-Za-z_][A-Za-z0-9_]{0,40})\b",
        text,
        flags=re.IGNORECASE,
    ):
        _add(m.start(1), m.group(1))
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9_]{1,40}\b", text):
        tok = m.group(0)
        if len(tok) < 2:
            continue
        if tok.lower() in stop:
            continue
        _add(m.start(), tok)

    cands.sort(key=lambda p: p[0])
    seen: dict[str, str] = {}
    for _, name in cands:
        key = name.lower()
        if key not in seen:
            seen[key] = name
    return list(seen.values())


def _canonical_verb(word: str) -> str | None:
    """Map an inflected generic verb to its canonical capability tag."""
    canonical = (
        "compute", "calculate", "validate", "check", "transform",
        "convert", "filter", "sort", "search", "find", "count",
        "parse", "store", "save", "load", "serve", "expose", "run",
        "play", "render", "display", "monitor", "schedule", "send",
        "receive", "add", "remove", "list", "update", "delete", "auth",
    )
    w = word.lower()
    if w in canonical:
        return w
    special = {
        "ran": "run",
        "running": "run",
        "runs": "run",
        "found": "find",
        "finding": "find",
        "finds": "find",
        "sent": "send",
        "sending": "send",
        "sends": "send",
        "authenticate": "auth",
        "authenticates": "auth",
        "authenticated": "auth",
        "authenticating": "auth",
        "authentication": "auth",
    }
    if w in special:
        return special[w]
    for suffix in ("ing", "ed", "es", "s", "d"):
        if w.endswith(suffix) and len(w) > len(suffix) + 1:
            stem = w[: -len(suffix)]
            if stem in canonical:
                return stem
            if (stem + "e") in canonical:
                return stem + "e"
            if (
                len(stem) >= 2
                and stem[-1] == stem[-2]
                and stem[:-1] in canonical
            ):
                return stem[:-1]
    return None


def _word_tokens(clause: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", clause)


def _object_after(tokens: list[str], idx: int) -> str:
    """Nearest noun-like token after position idx, case preserved."""
    stop = (
        "a", "an", "the", "to", "of", "in", "on", "for", "with",
        "and", "or", "then", "plus", "also", "as", "at", "by",
        "from", "into", "over", "under", "via", "per", "out", "up",
        "down", "off", "is", "are", "was", "were", "be", "been",
        "being", "it", "its", "this", "that", "these", "those",
        "they", "them", "he", "she", "we", "you", "i", "my",
        "your", "our", "their", "s", "t",
    )
    for tok in tokens[idx + 1:]:
        if len(tok) < 2:
            continue
        if tok.lower() in stop:
            continue
        if _canonical_verb(tok) is not None:
            continue
        return tok
    return ""


def _extract_behaviors(clauses: list[str]) -> list[dict]:
    behaviors: list[dict] = []
    for cl in clauses:
        tokens = _word_tokens(cl)
        verb: str | None = None
        vidx = -1
        for i, tok in enumerate(tokens):
            c = _canonical_verb(tok)
            if c is not None:
                verb = c
                vidx = i
                break
        if verb is None:
            verb = "handle"
            obj = _object_after(tokens, 0) if tokens else ""
        else:
            obj = _object_after(tokens, vidx)
        behaviors.append({"verb": verb, "object": obj, "detail": cl})
    return behaviors


def _has_word(low: str, word: str) -> bool:
    return re.search(r"\b" + re.escape(word) + r"\w*\b", low) is not None


def _detect_interfaces(
    spec: str, clauses: list[str]
) -> tuple[list[dict], str | None]:
    groups = (
        ("api", ("endpoint", "route", "http", "rest", "api")),
        ("cli", ("cli", "flag", "argument", "command", "subcommand")),
        ("store", ("table", "row", "sqlite", "database", "persist")),
        ("frontend", ("page", "render", "component")),
        ("bot", ("bot", "webhook")),
        ("pipeline", ("pipeline", "stage", "batch", "stream")),
        ("game", ("game", "play", "score")),
    )
    low = spec.lower()
    hits: dict[str, bool] = {}
    for kind, triggers in groups:
        hit = False
        for t in triggers:
            if _has_word(low, t):
                hit = True
                break
        if kind == "cli" and "--" in spec:
            hit = True
        hits[kind] = hit
    interfaces: list[dict] = []
    for kind, triggers in groups:
        if not hits[kind]:
            continue
        desc = kind + " interface"
        for cl in clauses:
            cl_low = cl.lower()
            found = False
            for t in triggers:
                if _has_word(cl_low, t):
                    found = True
                    break
            if kind == "cli" and "--" in cl:
                found = True
            if found:
                desc = cl[:140].strip()
                break
        interfaces.append({"kind": kind, "name": kind, "desc": desc})
    persistence: str | None = None
    if hits["store"]:
        if _has_word(low, "sqlite"):
            persistence = "sqlite"
        else:
            persistence = "local-store"
    return interfaces, persistence


def _infer_type(clause: str) -> str:
    low = clause.lower()
    if re.search(r"\bint(eger)?s?\b", low):
        return "int"
    if re.search(r"\bfloats?\b|\bdoubles?\b|\bnumbers?\b|\bdecimals?\b", low):
        return "float"
    if re.search(r"\bbools?\b|\bbooleans?\b", low):
        return "bool"
    if re.search(r"\blists?\b|\barrays?\b", low):
        return "list"
    if re.search(
        r"\bdicts?\b|\bdictionar(?:y|ies)\b|\bjson\b|\bobjects?\b", low
    ):
        return "dict"
    if re.search(r"\bfiles?\b", low):
        return "file"
    return "str"


def _extract_inputs_outputs(
    clauses: list[str],
) -> tuple[list[dict], list[dict]]:
    in_words = (
        "take", "takes", "taking", "receive", "receives", "received",
        "receiving", "input", "inputs",
    )
    out_words = (
        "return", "returns", "returning", "returned", "output",
        "outputs", "outputting", "print", "prints", "printed",
        "printing",
    )
    inputs: list[dict] = []
    outputs: list[dict] = []
    seen_in: dict[str, bool] = {}
    seen_out: dict[str, bool] = {}
    for cl in clauses:
        tokens = _word_tokens(cl)
        lowered = [t.lower() for t in tokens]
        in_idx = -1
        for i, tl in enumerate(lowered):
            if tl in in_words:
                in_idx = i
                break
        if in_idx >= 0:
            name = _object_after(tokens, in_idx) or "input"
            key = name.lower()
            if key not in seen_in:
                seen_in[key] = True
                inputs.append(
                    {"name": name, "type": _infer_type(cl), "desc": cl}
                )
        out_idx = -1
        for i, tl in enumerate(lowered):
            if tl in out_words:
                out_idx = i
                break
        if out_idx >= 0:
            name = _object_after(tokens, out_idx) or "output"
            key = name.lower()
            if key not in seen_out:
                seen_out[key] = True
                outputs.append(
                    {"name": name, "type": _infer_type(cl), "desc": cl}
                )
    return inputs, outputs


def _extract_constraints(clauses: list[str]) -> list[str]:
    signals = (
        "must", "only", "never", "always", "limit", "max", "min",
        "under", "over", "without", "cannot", "should", "required",
        "require", "requires", "constraint", "shall",
    )
    out: list[str] = []
    seen: dict[str, bool] = {}
    for cl in clauses:
        cl_low = cl.lower()
        hit = False
        for s in signals:
            if re.search(r"\b" + re.escape(s) + r"\b", cl_low):
                hit = True
                break
        if hit:
            item = cl[:140].strip()
            if item.lower() not in seen:
                seen[item.lower()] = True
                out.append(item)
    return out


def _extract_non_functional(clauses: list[str]) -> list[str]:
    signals = (
        "performance", "fast", "slow", "latency", "throughput",
        "scalable", "scalability", "reliable", "reliability",
        "secure", "security", "usability", "usable", "accessible",
        "accessibility", "maintainable", "maintainability",
        "robust", "efficient", "efficiency",
    )
    out: list[str] = []
    seen: dict[str, bool] = {}
    for cl in clauses:
        cl_low = cl.lower()
        hit = False
        for s in signals:
            if re.search(r"\b" + re.escape(s) + r"\b", cl_low):
                hit = True
                break
        if hit:
            item = cl[:140].strip()
            if item.lower() not in seen:
                seen[item.lower()] = True
                out.append(item)
    return out


def _build_data_entities(
    entities: list[str], clauses: list[str]
) -> list[dict]:
    out: list[dict] = []
    for ent in entities:
        desc = ""
        for cl in clauses:
            if ent.lower() in cl.lower():
                desc = cl[:140].strip()
                break
        ent_fields: list[str] = []
        seen_f: dict[str, bool] = {}
        for cl in clauses:
            if ent.lower() not in cl.lower():
                continue
            for m in re.finditer(r"\(([^()\n]{1,120})\)", cl):
                inner = m.group(1)
                for piece in re.split(r"[,;]+", inner):
                    p = piece.strip()
                    mm = re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", p)
                    if mm and len(ent_fields) < 10:
                        if p.lower() not in seen_f:
                            seen_f[p.lower()] = True
                            ent_fields.append(p)
                if ent_fields:
                    break
            if ent_fields:
                break
        out.append({"name": ent, "fields": ent_fields, "desc": desc})
    return out


def analyze(spec: str) -> Requirements:
    """Analyze a spec string into a Requirements record (deterministic)."""
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("spec must be a non-empty string")
    truncated = len(spec) > 5000
    working = spec[:5000] if truncated else spec

    clauses = _split_clauses(working)
    substantive = [c for c in clauses if _is_substantive(c)]
    goals: list[str] = []
    for c in substantive[:3]:
        goals.append(c[:140].strip())
    if not goals:
        if clauses:
            goals = [clauses[0][:140].strip()]
        else:
            goals = [working.strip()[:140]]

    title = goals[0][:80].strip() if goals and goals[0].strip() else "untitled"

    entities = _extract_entities(working)
    behaviors = _extract_behaviors(clauses)
    interfaces, persistence = _detect_interfaces(working, clauses)
    inputs, outputs = _extract_inputs_outputs(clauses)
    constraints = _extract_constraints(clauses)
    non_functional = _extract_non_functional(clauses)
    data_entities = _build_data_entities(entities, clauses)

    ambiguities: list[str] = []
    assumptions: list[str] = []

    defaulted_input = False
    if not inputs:
        inputs = [
            {"name": "request", "type": "str", "desc": "default input"}
        ]
        defaulted_input = True
        ambiguities.append(
            "no explicit inputs found; defaulted to 'request' of type str"
        )

    for b in behaviors:
        if not b.get("object"):
            detail = str(b.get("detail", ""))[:60]
            ambiguities.append(
                "vague action '%s' without object in: '%s'"
                % (b.get("verb", "handle"), detail)
            )
    if not outputs:
        ambiguities.append("no explicit outputs described")

    scale_pairs = (
        ("always", "never"),
        ("all", "none"),
        ("single", "multiple"),
        ("single", "many"),
        ("small", "large"),
        ("simple", "complex"),
        ("fast", "slow"),
        ("sync", "async"),
        ("single-user", "multi-user"),
    )
    low = working.lower()
    for a, b in scale_pairs:
        if a in low and b in low:
            ambiguities.append(
                "contradictory scale terms: '%s' vs '%s'" % (a, b)
            )

    pronouns = ("it", "they", "them", "this", "that", "these", "those")
    found_pronouns: list[str] = []
    for p in pronouns:
        if re.search(r"\b" + re.escape(p) + r"\b", low):
            found_pronouns.append(p)
    for p in sorted(found_pronouns):
        ambiguities.append("unresolved pronoun '%s'" % p)

    assumptions.append("runtime defaults to python (stdlib-only)")
    assumptions.append("stdlib-first: no third-party dependencies assumed")
    assumptions.append("single-user local execution assumed")
    if persistence is None:
        assumptions.append(
            "no persistence requested; ephemeral in-memory assumed"
        )
    if defaulted_input:
        assumptions.append(
            "default input 'request:str' assumed (no explicit inputs found)"
        )
    if truncated:
        assumptions.append(
            "input truncated to 5000 characters for deterministic analysis"
        )

    return Requirements(
        title=title,
        goals=goals,
        constraints=constraints,
        inputs=inputs,
        outputs=outputs,
        interfaces=interfaces,
        dependencies=[],
        runtime="python",
        persistence=persistence,
        data_entities=data_entities,
        behaviors=behaviors,
        non_functional=non_functional,
        ambiguities=ambiguities,
        assumptions=assumptions,
        raw=working,
    )


def detect_gaps(req: Requirements) -> list[str]:
    """Report missing outputs/interfaces/persistence decisions."""
    gaps: list[str] = []
    if not req.outputs:
        gaps.append("missing outputs: no outputs described")
    if not req.interfaces:
        gaps.append("missing interfaces: no interface signals detected")
    if req.persistence is None:
        gaps.append(
            "persistence undecided: no persistence requested "
            "(ephemeral assumed)"
        )
    return gaps
