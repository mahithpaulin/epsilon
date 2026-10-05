"""Epsilon v2 project planner: requirements input -> typed HIR project skeleton.

Pipeline stage: plan. This module never emits source text; it composes
hir nodes (Project / SourceFile / DataModel / ApiEndpoint / CliCommand /
TestSpec / ConfigFile / DocFile) that later stages render and verify.

Project-type detection is score based: every candidate type accumulates
``weight * occurrences`` over capability verbs, interface signals and
persistence text. A type wins only with >= 2 distinct supporting signals,
otherwise the plan falls back to ``library`` and records an assumption.
Ties resolve by a fixed priority order (see _PRIORITY), so no single
keyword ever decides the outcome on its own.

The ``requirements`` and ``snippets`` companion modules are optional: when
present their vocabulary is reused, otherwise built-in tables apply. Only
the standard library is used. All planners are pure functions of their
inputs; module-level tables are immutable and never mutated, so repeated
calls are deterministic and share no state.
"""
from __future__ import annotations

import keyword
import re

try:  # normal use: imported as part of the epsilon package
    from . import hir as H
except ImportError:  # loaded stand-alone, e.g. direct module path
    import hir as H  # type: ignore

try:
    from . import requirements as _requirements
except Exception:
    try:
        import requirements as _requirements  # type: ignore
    except Exception:
        _requirements = None

try:
    from . import snippets as _snippets
except Exception:
    try:
        import snippets as _snippets  # type: ignore
    except Exception:
        _snippets = None


# ---------------------------------------------------------------------------
# Fixed tables (immutable; never mutated at runtime)
# ---------------------------------------------------------------------------

PROJECT_TYPES = ('cli', 'api', 'library', 'pipeline', 'tool', 'game',
                 'db', 'service', 'bot', 'frontend', 'algorithmic')

# Tie-break priority, highest first. Interface-driven types come before
# generic ones; 'library' is last because it is also the fallback.
_PRIORITY = ('cli', 'api', 'game', 'bot', 'frontend', 'service', 'db',
             'pipeline', 'tool', 'algorithmic', 'library')

# (project type, signal substring, weight). Signals are matched against a
# lowercased corpus built from title, description, capability verbs,
# interface declarations, persistence text, entities and behaviors.
# Score per type = sum(weight * min(occurrences, 3)).
_SIGNALS = (
    ('cli', 'cli', 3), ('cli', 'command-line', 3), ('cli', 'command line', 3),
    ('cli', 'argparse', 3), ('cli', 'argv', 2), ('cli', 'subcommand', 3),
    ('cli', 'console script', 2), ('cli', 'console app', 2),
    ('cli', 'terminal', 2), ('cli', 'shell command', 2),
    ('cli', 'command', 1), ('cli', 'flag', 2), ('cli', 'argument', 2),
    ('api', 'api', 3), ('api', 'rest', 3), ('api', 'endpoint', 3),
    ('api', 'http', 2), ('api', 'route', 2), ('api', 'openapi', 3),
    ('api', 'json api', 2), ('api', 'status code', 2),
    ('api', 'get /', 2), ('api', 'post /', 2),
    ('api', 'request', 1), ('api', 'response', 1),
    ('library', 'library', 3), ('library', 'reusable', 2),
    ('library', 'sdk', 2), ('library', 'pip install', 3),
    ('library', 'package', 2), ('library', 'import', 1),
    ('library', 'module', 1),
    ('pipeline', 'pipeline', 3), ('pipeline', 'etl', 3),
    ('pipeline', 'data pipeline', 3), ('pipeline', 'batch', 2),
    ('pipeline', 'stream', 2), ('pipeline', 'workflow', 2),
    ('pipeline', 'stage', 1),
    ('tool', 'tool', 3), ('tool', 'utility', 2), ('tool', 'convert', 2),
    ('tool', 'formatter', 2), ('tool', 'lint', 2), ('tool', 'script', 2),
    ('tool', 'generator', 2),
    ('game', 'game', 3), ('game', 'player', 2), ('game', 'score', 2),
    ('game', 'level', 2), ('game', 'turn', 2), ('game', 'board', 2),
    ('game', 'enemy', 2), ('game', 'sprite', 3),
    ('game', 'move', 1), ('game', 'win', 1), ('game', 'lose', 1),
    ('db', 'database', 3), ('db', 'sqlite', 3), ('db', 'sql', 2),
    ('db', 'schema', 2), ('db', 'query', 2), ('db', 'migration', 3),
    ('db', 'persist', 2), ('db', 'table', 1), ('db', 'store', 1),
    ('service', 'service', 3), ('service', 'daemon', 3),
    ('service', 'server', 2), ('service', 'background', 2),
    ('service', 'listen', 2), ('service', 'deploy', 2),
    ('service', 'systemd', 3), ('service', 'port', 1),
    ('bot', 'bot', 3), ('bot', 'discord', 3), ('bot', 'slack', 3),
    ('bot', 'telegram', 3), ('bot', 'command prefix', 3),
    ('bot', 'webhook', 2), ('bot', 'chat', 2),
    ('bot', 'message', 1), ('bot', 'reply', 1),
    ('frontend', 'frontend', 3), ('frontend', 'user interface', 3),
    ('frontend', 'web page', 2), ('frontend', 'react', 2),
    ('frontend', 'html', 2), ('frontend', 'css', 2),
    ('frontend', 'component', 2), ('frontend', 'render', 1),
    ('frontend', 'button', 1), ('frontend', 'form', 1),
    ('algorithmic', 'algorithm', 3), ('algorithmic', 'factorial', 3),
    ('algorithmic', 'fibonacci', 3), ('algorithmic', 'fizzbuzz', 3),
    ('algorithmic', 'recursion', 2), ('algorithmic', 'calculate', 2),
    ('algorithmic', 'prime', 2), ('algorithmic', 'palindrome', 2),
    ('algorithmic', 'compute', 1), ('algorithmic', 'sort', 1),
    ('algorithmic', 'search', 1),
)

# Generic snippet vocabulary used inside FuncDef behaviors lists. When the
# optional snippets module exposes its own vocabulary it takes precedence.
_BUILTIN_VOCABULARY = (
    'validate-inputs', 'compute', 'transform-each', 'filter-where',
    'accumulate', 'count-where', 'none-where', 'search-first', 'sort-by',
    'dedupe', 'group-count', 'reverse-seq', 'average', 'minmax-loop',
    'branch-return', 'repeat-range', 'recurse', 'guard-raise', 'raise',
)

# Capability verb -> vocabulary behavior. Ordered specific-first;
# first regex match wins, unmatched verbs map to 'compute'.
_VERB_PATTERNS = (
    (r'valid', 'validate-inputs'),
    (r'guard', 'guard-raise'),
    (r'rais', 'raise'),
    (r'\berror\b', 'raise'),
    (r'recurs', 'recurse'),
    (r'factorial', 'recurse'),
    (r'fibonacci', 'recurse'),
    (r'dedup', 'dedupe'),
    (r'\bunique\b', 'dedupe'),
    (r'group', 'group-count'),
    (r'averag', 'average'),
    (r'\bmean\b', 'average'),
    (r'minmax', 'minmax-loop'),
    (r'\bmax\b', 'minmax-loop'),
    (r'\bmin\b', 'minmax-loop'),
    (r'\blargest\b', 'minmax-loop'),
    (r'\bsmallest\b', 'minmax-loop'),
    (r'transform', 'transform-each'),
    (r'\bmap\b', 'transform-each'),
    (r'\bupdate\b', 'transform-each'),
    (r'\bedit\b', 'transform-each'),
    (r'advance', 'transform-each'),
    (r'filter', 'filter-where'),
    (r'\bdelete\b', 'filter-where'),
    (r'\bremove\b', 'filter-where'),
    (r'accumul', 'accumulate'),
    (r'\bsum\b', 'accumulate'),
    (r'\btotal\b', 'accumulate'),
    (r'\badd\b', 'accumulate'),
    (r'\bcreate\b', 'accumulate'),
    (r'\bappend\b', 'accumulate'),
    (r'\bscore\b', 'accumulate'),
    (r'count', 'count-where'),
    (r'\bempty\b', 'none-where'),
    (r'\bnone\b', 'none-where'),
    (r'\bexists\b', 'none-where'),
    (r'search', 'search-first'),
    (r'\bfind\b', 'search-first'),
    (r'\blookup\b', 'search-first'),
    (r'sort', 'sort-by'),
    (r'\border\b', 'sort-by'),
    (r'revers', 'reverse-seq'),
    (r'branch', 'branch-return'),
    (r'\bchoose\b', 'branch-return'),
    (r'\bmove\b', 'branch-return'),
    (r'repeat', 'repeat-range'),
    (r'\bloop\b', 'repeat-range'),
    (r'\brange\b', 'repeat-range'),
    (r'\brun\b', 'repeat-range'),
    (r'\bplay\b', 'repeat-range'),
    (r'\bstep\b', 'compute'),
    (r'\blist\b', 'compute'),
    (r'\bshow\b', 'compute'),
    (r'\bget\b', 'compute'),
    (r'\bset\b', 'compute'),
    (r'\bsave\b', 'compute'),
    (r'\bload\b', 'compute'),
)

_CRUD_COMMANDS = ('add', 'list', 'update', 'remove')

_DEFAULT_MAX_FILES = 40


# ---------------------------------------------------------------------------
# Requirement access helpers (req may be a dict or an attribute object)
# ---------------------------------------------------------------------------

def _req_get(req, *keys, **kw):
    default = kw.get('default')
    for key in keys:
        if isinstance(req, dict):
            if key in req and req[key] is not None:
                return req[key]
        else:
            value = getattr(req, key, None)
            if value is not None:
                return value
    return default


def _as_text_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        out = []
        for key, val in value.items():
            out.append(str(key))
            out.extend(_as_text_list(val))
        return out
    try:
        items = list(value)
    except TypeError:
        return [str(value)]
    out = []
    for item in items:
        if isinstance(item, dict):
            picked = False
            for pick in ('verb', 'name', 'behavior', 'command', 'title'):
                if item.get(pick):
                    out.append(str(item[pick]))
                    picked = True
                    break
            if not picked:
                out.append(' '.join(str(v) for v in item.values()))
            # descriptions carry the signals (flags, paths, formats); the
            # name alone ('cli') would starve type detection.
            for extra in ('desc', 'description', 'detail', 'object'):
                if item.get(extra):
                    out.append(str(item[extra]))
        else:
            out.append(str(item))
    return [t for t in out if t.strip()]


def _capabilities_of(req):
    return _as_text_list(
        _req_get(req, 'capabilities', 'verbs', 'features', 'actions',
                 default=[]))


def _behaviors_of(req):
    raw = _req_get(req, 'behaviors', 'operations', default=[])
    verbs = _as_text_list(raw)
    if verbs:
        return verbs
    return _capabilities_of(req)


def _interfaces_of(req):
    raw = _req_get(req, 'interfaces', 'interface', 'surfaces', default={})
    if isinstance(raw, dict):
        return {str(k).lower(): v for k, v in raw.items()}
    if isinstance(raw, (list, tuple)):
        return {'cli': list(raw)}
    if isinstance(raw, str) and raw.strip():
        return {'cli': [raw]}
    return {}


def _entities_of(req):
    raw = _req_get(req, 'entities', 'models', 'nouns', 'objects', default=[])
    if isinstance(raw, str):
        return [(raw, [])]
    try:
        items = list(raw)
    except TypeError:
        return []
    out = []
    for item in items:
        if isinstance(item, dict):
            name = item.get('name') or item.get('title') or item.get('entity')
            fields = item.get('fields') or item.get('attrs') or item.get('columns') or []
            out.append((str(name or 'Item'), fields))
        else:
            out.append((str(item), []))
    return [(n, f) for n, f in out if n.strip()]


def _persistence_text(req):
    raw = _req_get(req, 'persistence', 'storage', 'store', 'database',
                   'backend', default='')
    if isinstance(raw, dict):
        raw = ' '.join(str(v) for v in raw.values())
    elif not isinstance(raw, str):
        try:
            raw = ' '.join(str(v) for v in list(raw))
        except TypeError:
            raw = str(raw)
    return raw or ''


def _endpoints_of(req):
    found = []
    raw = _req_get(req, 'endpoints', 'routes', default=[])
    if isinstance(raw, str):
        raw = [raw]
    try:
        found.extend(list(raw or []))
    except TypeError:
        pass
    for key in ('api', 'http', 'rest'):
        extra = _interfaces_of(req).get(key)
        if isinstance(extra, str):
            found.append(extra)
        elif extra:
            try:
                found.extend(list(extra))
            except TypeError:
                pass
    return found


def _title_of(req):
    title = _req_get(req, 'title', 'name', 'project', 'project_name',
                     default='')
    title = str(title or '').strip()
    return title or 'app'


def _description_of(req):
    desc = _req_get(req, 'description', 'spec', 'summary', 'brief',
                    default='')
    return str(desc or '').strip()


def _max_files_of(req):
    for key in ('max_files', 'maxFiles'):
        value = _req_get(req, key, default=None)
        if isinstance(value, int) and value > 0:
            return value
    cfg = _req_get(req, 'config', default=None)
    if isinstance(cfg, dict):
        value = cfg.get('max_files', cfg.get('maxFiles'))
        if isinstance(value, int) and value > 0:
            return value
    else:
        value = getattr(cfg, 'max_files', None)
        if isinstance(value, int) and value > 0:
            return value
    return _DEFAULT_MAX_FILES


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

def _project_slug(title, entities=()):
    """Short filesystem slug: named entity wins, else first content words.

    Only the directory is folded; code identifiers keep user spelling.
    """
    m = re.search(r'(?i)\bnamed\s+([A-Za-z0-9_][\w-]*)', title or '')
    if m:
        return _package_dir(m.group(1))
    m = re.search(r'(?i)\bcalled\s+([A-Za-z0-9_][\w-]*)', title or '')
    if m:
        return _package_dir(m.group(1))
    for ent in entities or []:
        nm = ent.get('name') if isinstance(ent, dict) else None
        if nm and re.match(r'^[A-Za-z][A-Za-z0-9_]{1,24}$', str(nm)):
            low = str(nm).lower()
            if low not in ('item', 'cli', 'api', 'app', 'tool', 'rest',
                           'json', 'get', 'post'):
                return _package_dir(str(nm))
    words = [w for w in re.findall(r'[A-Za-z0-9]+', title or '')
             if w.lower() not in ('a', 'an', 'the', 'with', 'and', 'that',
                                   'with', 'named', 'called')][:3]
    if words:
        return _package_dir('_'.join(words))
    return _package_dir(title)


def _package_dir(title):
    """Filesystem directory: lowercased, sanitized; only this is folded."""
    text = re.sub(r'[^0-9a-zA-Z]+', '_', (title or 'app').strip().lower())
    text = text.strip('_') or 'app'
    if text[0].isdigit():
        text = 'pkg_' + text
    if keyword.iskeyword(text):
        text = text + '_pkg'
    return text


def _snake(name, default='item'):
    """Readable snake_case for files and function stems; keeps word parts."""
    text = re.sub(r'[^0-9a-zA-Z]+', '_', (name or '').strip())
    text = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', text)
    text = re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', '_', text)
    text = re.sub(r'_+', '_', text.lower()).strip('_') or default
    if text[0].isdigit():
        text = 'm_' + text
    if keyword.iskeyword(text):
        text = text + '_fn'
    return text


def _code_name(name, default='Item'):
    """Code identifier that preserves the user's entity spelling and case."""
    text = re.sub(r'[^0-9a-zA-Z_]+', '_', (name or '').strip()).strip('_')
    if not text:
        text = default
    if text[0].isdigit():
        text = 'M_' + text
    if keyword.iskeyword(text):
        text = text + '_'
    return text


def _plural(snake):
    if re.search(r'[^aeiou]y$', snake):
        return snake[:-1] + 'ies'
    if snake.endswith('s'):
        return snake + 'es'
    return snake + 's'


def _normalize_language(language):
    text = str(language or 'python').strip().lower()
    mapping = {'python': 'python', 'py': 'python',
               'javascript': 'javascript', 'js': 'javascript',
               'typescript': 'typescript', 'ts': 'typescript'}
    if text in mapping:
        return mapping[text], None
    return 'python', ('unknown language %r; assuming python' % (language,))


# ---------------------------------------------------------------------------
# Vocabulary / verb mapping
# ---------------------------------------------------------------------------

def _vocabulary():
    """Snippet vocabulary, preferring the optional snippets module."""
    mod = _snippets
    if mod is not None:
        for attr in ('VOCABULARY', 'BEHAVIORS', 'SNIPPETS', 'KINDS'):
            table = getattr(mod, attr, None)
            if table:
                try:
                    names = list(table)
                    if names and all(isinstance(n, str) for n in names):
                        return tuple(names)
                except TypeError:
                    pass
        func = getattr(mod, 'vocabulary', None)
        if callable(func):
            try:
                names = func()
                if names:
                    return tuple(names)
            except Exception:
                pass
    return _BUILTIN_VOCABULARY


def _map_verb_to_behavior(verb):
    text = ' %s ' % (verb or '').strip().lower()
    for pattern, behavior in _VERB_PATTERNS:
        if re.search(pattern, text):
            return behavior
    return 'compute'


def _behavior_entry(behavior, entity):
    vocab = _vocabulary()
    name = behavior if behavior in vocab else _map_verb_to_behavior(behavior)
    entry = {'behavior': name}
    if entity:
        entry['on'] = entity
    return entry


# ---------------------------------------------------------------------------
# Type detection
# ---------------------------------------------------------------------------

def _signal_corpus(req):
    parts = [_title_of(req), _description_of(req)]
    parts.extend(_capabilities_of(req))
    parts.extend(_behaviors_of(req))
    ifaces = _interfaces_of(req)
    for key, val in ifaces.items():
        parts.append(str(key))
        parts.extend(_as_text_list(val))
    parts.append(_persistence_text(req))
    for name, _fields in _entities_of(req):
        parts.append(name)
    parts.extend(_as_text_list(_endpoints_of(req)))
    return '\n'.join(p for p in parts if p).lower()


def _score_types(req):
    corpus = _signal_corpus(req)
    scores = {t: 0 for t in PROJECT_TYPES}
    matched = {t: [] for t in PROJECT_TYPES}
    for ptype, signal, weight in _SIGNALS:
        count = corpus.count(signal)
        if count > 0:
            scores[ptype] += weight * min(count, 3)
            matched[ptype].append(signal)
    for ptype in PROJECT_TYPES:
        matched[ptype] = sorted(set(matched[ptype]))
    return scores, matched


def _detection_detail(req):
    scores, matched = _score_types(req)
    # Title bonus: a title that names the kind outright ("Todo CLI",
    # "Notes REST API") is independent evidence from body/interface signals.
    # Still scoring-based (never single-keyword dispatch): it adds one more
    # supporting signal, so the >=2 rule below still requires corroboration.
    title = _title_of(req).lower()
    for ptype, keyword in (('cli', 'cli'), ('api', 'api'), ('game', 'game'),
                           ('bot', 'bot'), ('pipeline', 'pipeline'),
                           ('db', 'database'), ('service', 'service'),
                           ('frontend', 'frontend'), ('tool', 'tool'),
                           ('library', 'library')):
        if re.search(r'\b' + re.escape(keyword) + r'\b', title):
            scores[ptype] += 2
            if ('title:' + keyword) not in matched[ptype]:
                matched[ptype].append('title:' + keyword)
    best = 'library'
    best_score = -1
    for ptype in _PRIORITY:
        if scores[ptype] > best_score:
            best = ptype
            best_score = scores[ptype]
    fell_back = best_score <= 0 or len(matched[best]) < 2
    return ('library' if fell_back else best), scores, matched, fell_back


def detect_type(req) -> str:
    """Score-based project-type detection over the requirement corpus.

    Scoring: each candidate type sums ``weight * min(occurrences, 3)``
    over its signal substrings (see _SIGNALS). The winner needs at least
    two distinct supporting signals; otherwise the result falls back to
    ``library`` so a lone keyword can never decide the outcome.
    Ties resolve by the fixed _PRIORITY order, highest first.
    """
    ptype, _scores, _matched, _fell_back = _detection_detail(req)
    return ptype


# ---------------------------------------------------------------------------
# Decisions / events
# ---------------------------------------------------------------------------

def _emit(events, stage, kind, message, data=None):
    if events is None:
        return None
    emit = getattr(events, 'emit', None)
    if not callable(emit):
        return None
    try:
        return emit(stage, kind, message, dict(data or {}))
    except Exception:
        return None


def _recorder(meta, events):
    decisions = meta.setdefault('decisions', [])

    def decide(choice, value, reason):
        decisions.append({'kind': 'decision', 'choice': choice,
                          'value': str(value), 'reason': reason})
        _emit(events, 'plan', 'decided', '%s -> %s (%s)'
              % (choice, value, reason),
              {'choice': choice, 'value': str(value), 'reason': reason})

    def assume(choice, value, reason):
        decisions.append({'kind': 'assumption', 'choice': choice,
                          'value': str(value), 'reason': reason})
        _emit(events, 'plan', 'decided', 'assume %s = %s (%s)'
              % (choice, value, reason),
              {'choice': choice, 'value': str(value), 'reason': reason,
               'assumption': True})

    return decide, assume


def _append_node(project, node):
    if isinstance(node, H.DataModel):
        project.models.append(node)
    elif isinstance(node, H.ApiEndpoint):
        project.endpoints.append(node)
    elif isinstance(node, H.CliCommand):
        project.cli.append(node)
    elif isinstance(node, H.TestSpec):
        project.tests.append(node)
    elif isinstance(node, (H.SourceFile, H.ConfigFile, H.DocFile)):
        project.files.append(node)
    return node


# ---------------------------------------------------------------------------
# Params / returns / test-case derivation
# ---------------------------------------------------------------------------

def _type_for_param(name):
    low = (name or '').lower()
    if low in ('items', 'rows', 'records', 'entries', 'values', 'args',
               'results', 'todos', 'notes') or low.endswith('_list'):
        return 'list'
    if (low.endswith('_id') or low in ('count', 'total', 'index', 'size',
                                       'n', 'limit', 'offset', 'status')):
        return 'int'
    if low in ('ratio', 'score_value', 'average'):
        return 'float'
    if low.startswith(('is_', 'has_', 'done')) or low in ('done', 'active'):
        return 'bool'
    if low in ('mapping', 'payload', 'body', 'state', 'config'):
        return 'dict'
    return 'str'


def _params_for_verb(verb, behavior):
    low = (verb or '').lower()
    if re.search(r'\b(add|create|append)\b', low):
        return (('name', 'str'),)
    if re.search(r'\b(update|edit|rename)\b', low):
        return (('item_id', 'int'), ('name', 'str'))
    if re.search(r'\b(delete|remove)\b', low):
        return (('item_id', 'int'),)
    if re.search(r'\b(search|find|lookup|filter)\b', low):
        return (('items', 'list'), ('query', 'str'))
    if behavior in ('transform-each', 'filter-where', 'search-first',
                    'sort-by', 'dedupe', 'group-count', 'reverse-seq',
                    'average', 'minmax-loop', 'accumulate', 'count-where',
                    'none-where'):
        if behavior in ('search-first', 'filter-where', 'count-where'):
            return (('items', 'list'), ('query', 'str'))
        return (('items', 'list'),)
    if re.search(r'\b(load|read)\b', low):
        return (('path', 'str'),)
    if re.search(r'\b(save|write|persist)\b', low):
        return (('items', 'list'), ('path', 'str'))
    if behavior in ('recurse', 'repeat-range'):
        return (('n', 'int'),)
    if behavior in ('validate-inputs', 'guard-raise'):
        return (('value', 'str'),)
    if re.search(r'\b(list|show|sort|count|average|mean|group|reverse|max|min)\b', low):
        return (('items', 'list'),)
    return (('value', 'str'),)


def _returns_for(verb, behavior, entity_code):
    if behavior in ('recurse', 'accumulate', 'count-where',
                    'minmax-loop', 'repeat-range'):
        return 'int'
    if behavior == 'average':
        return 'float'
    if behavior in ('validate-inputs', 'guard-raise', 'none-where'):
        return 'bool'
    if behavior in ('transform-each', 'filter-where', 'search-first',
                    'sort-by', 'dedupe', 'group-count', 'reverse-seq'):
        return 'list'
    if behavior == 'raise':
        return None
    low = (verb or '').lower()
    if re.search(r'\b(add|create|append)\b', low):
        return entity_code
    if re.search(r'\b(delete|remove|update|edit)\b', low):
        return 'bool'
    if behavior == 'branch-return':
        return 'str'
    return 'str'


def _sample_for_type(typename):
    if typename == 'int':
        return 3
    if typename == 'float':
        return 2.5
    if typename == 'bool':
        return True
    if typename == 'list':
        return [3, 1, 2]
    if typename == 'dict':
        return {'key': 'value'}
    return 'sample'


def _zero_for_type(typename):
    if typename == 'int':
        return 0
    if typename == 'float':
        return 0.0
    if typename == 'bool':
        return False
    if typename == 'list':
        return []
    if typename == 'dict':
        return {}
    return ''


def _literal_src(value):
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if value is None:
        return 'None'
    return repr(value)


def _cases_for_func(func_name, verb, params, behavior):
    """Behavioral cases: normal + edge + invalid derived from the params."""
    low_verb = (verb or '').lower()
    first = params[0][0] if params else 'value'
    cases = []

    def case(given, expect=None, raises=None, note=''):
        kwargs = {'given': given, 'note': note}
        if expect is not None:
            kwargs['expect'] = expect
        if raises is not None:
            kwargs['raises'] = raises
        return H.TestCase(**kwargs)

    if re.search(r'\b(add|create|append)\b', low_verb):
        cases.append(case({'name': 'Buy milk'},
                          "{'id': 1, 'name': 'Buy milk'}",
                          note='normal: add returns the stored record'))
        cases.append(case({'name': ''}, raises='ValueError',
                          note='edge: empty name is rejected'))
        cases.append(case({'name': None}, raises='TypeError',
                          note='invalid: null name raises'))
        return cases
    if re.search(r'\b(delete|remove)\b', low_verb):
        cases.append(case({'item_id': 1}, 'True',
                          note='normal: removing a known id succeeds'))
        cases.append(case({'item_id': 999}, 'False',
                          note='edge: unknown key removes nothing'))
        cases.append(case({'item_id': -1}, raises='ValueError',
                          note='invalid: negative id raises'))
        return cases
    if re.search(r'\b(update|edit)\b', low_verb):
        cases.append(case({'item_id': 1, 'name': 'New name'}, 'True',
                          note='normal: updating a known id succeeds'))
        cases.append(case({'item_id': 999, 'name': 'New name'}, 'False',
                          note='edge: unknown key updates nothing'))
        cases.append(case({'item_id': 1, 'name': ''}, raises='ValueError',
                          note='invalid: empty name raises'))
        return cases
    if re.search(r'\b(search|find|lookup)\b', low_verb):
        cases.append(case({'items': [1, 2, 3], 'query': 2}, '2',
                          note='normal: search finds the first match'))
        cases.append(case({'items': [], 'query': 2}, 'None',
                          note='edge: empty list finds nothing'))
        cases.append(case({'items': [1], 'query': None}, raises='ValueError',
                          note='invalid: null query raises'))
        return cases
    if behavior == 'sort-by':
        cases.append(case({'items': [3, 1, 2]}, '[1, 2, 3]',
                          note='normal: sort orders the sample'))
        cases.append(case({'items': []}, '[]',
                          note='edge: empty list sorts to empty'))
        cases.append(case({'items': None}, raises='TypeError',
                          note='invalid: null input raises'))
        return cases
    if behavior == 'filter-where':
        cases.append(case({'items': [1, 2, 3], 'query': 2}, '[2]',
                          note='normal: filter keeps matches'))
        cases.append(case({'items': [], 'query': 2}, '[]',
                          note='edge: empty list filters to empty'))
        cases.append(case({'items': None, 'query': 2}, raises='TypeError',
                          note='invalid: null input raises'))
        return cases
    if behavior == 'count-where':
        cases.append(case({'items': [1, 2, 3], 'query': 2}, '1',
                          note='normal: count of matches'))
        cases.append(case({'items': [], 'query': 2}, '0',
                          note='edge: empty list counts zero'))
        cases.append(case({'items': None, 'query': 2}, raises='TypeError',
                          note='invalid: null input raises'))
        return cases
    if behavior == 'accumulate':
        cases.append(case({'items': [1, 2, 3]}, '6',
                          note='normal: accumulation over sample'))
        cases.append(case({'items': []}, '0',
                          note='edge: empty list accumulates to zero'))
        cases.append(case({'items': None}, raises='TypeError',
                          note='invalid: null input raises'))
        return cases
    if behavior == 'average':
        cases.append(case({'items': [1.0, 2.0, 3.0]}, '2.0',
                          note='normal: average of sample'))
        cases.append(case({'items': [5.0]}, '5.0',
                          note='edge: single item averages to itself'))
        cases.append(case({'items': []}, raises='ValueError',
                          note='invalid: empty average raises'))
        return cases
    if behavior == 'minmax-loop':
        cases.append(case({'items': [3, 1, 2]}, '3',
                          note='normal: extremum of sample'))
        cases.append(case({'items': [7]}, '7',
                          note='edge: single item is its own extremum'))
        cases.append(case({'items': []}, raises='ValueError',
                          note='invalid: empty input raises'))
        return cases
    if behavior == 'dedupe':
        cases.append(case({'items': [1, 1, 2]}, '[1, 2]',
                          note='normal: duplicates collapsed'))
        cases.append(case({'items': []}, '[]',
                          note='edge: empty list stays empty'))
        cases.append(case({'items': None}, raises='TypeError',
                          note='invalid: null input raises'))
        return cases
    if behavior == 'reverse-seq':
        cases.append(case({'items': [1, 2, 3]}, '[3, 2, 1]',
                          note='normal: order reversed'))
        cases.append(case({'items': []}, '[]',
                          note='edge: empty list stays empty'))
        cases.append(case({'items': None}, raises='TypeError',
                          note='invalid: null input raises'))
        return cases
    if behavior == 'group-count':
        cases.append(case({'items': [1, 1, 2]}, '{1: 2, 2: 1}',
                          note='normal: groups counted'))
        cases.append(case({'items': []}, '{}',
                          note='edge: empty list groups to empty'))
        cases.append(case({'items': None}, raises='TypeError',
                          note='invalid: null input raises'))
        return cases
    if behavior == 'none-where':
        cases.append(case({'items': [1], 'query': 9}, 'True',
                          note='normal: no item matches'))
        cases.append(case({'items': [], 'query': 9}, 'True',
                          note='edge: empty list trivially matches none'))
        cases.append(case({'items': None, 'query': 9}, raises='TypeError',
                          note='invalid: null input raises'))
        return cases
    if behavior in ('validate-inputs', 'guard-raise'):
        sample = {name: _sample_for_type(t) for name, t in params} or \
            {first: 'sample'}
        cases.append(case(sample, 'True',
                          note='normal: valid input accepted'))
        cases.append(case({name: _zero_for_type(t) for name, t in params},
                          'False', note='edge: zero input rejected'))
        cases.append(case({first: None}, raises='ValueError',
                          note='invalid: null input raises'))
        return cases
    if behavior == 'raise':
        cases.append(case({first: _sample_for_type(params[0][1])}
                          if params else {'value': 'x'},
                          raises='ValueError',
                          note='normal: guard fires on bad input'))
        cases.append(case({first: _zero_for_type(params[0][1])}
                          if params else {'value': ''},
                          raises='ValueError',
                          note='edge: guard fires on zero input'))
        cases.append(case({first: None}, raises='TypeError',
                          note='invalid: null type raises'))
        return cases
    if behavior == 'recurse':
        low_name = (func_name or '').lower()
        if 'factorial' in low_name or 'fact' in low_name.split('_'):
            cases.append(case({'n': 5}, '120',
                              note='normal: factorial grows recursively'))
            cases.append(case({'n': 0}, '1',
                              note='edge: zero is the base case'))
            cases.append(case({'n': -1}, raises='ValueError',
                              note='invalid: negative input raises'))
            return cases
        if 'fib' in low_name:
            cases.append(case({'n': 7}, '13',
                              note='normal: fibonacci grows recursively'))
            cases.append(case({'n': 0}, '0',
                              note='edge: zero is a base case'))
            cases.append(case({'n': -1}, raises='ValueError',
                              note='invalid: negative input raises'))
            return cases
        cases.append(case({'n': 3}, '6',
                          note='normal: recursion over small input'))
        cases.append(case({'n': 0}, '1',
                          note='edge: zero is the base case'))
        cases.append(case({'n': -1}, raises='ValueError',
                          note='invalid: negative input raises'))
        return cases
    # Generic fallback: sample in, literal out; zero in, zero out.
    sample = {name: _sample_for_type(t) for name, t in params}
    if not sample:
        sample = {'value': 'sample'}
    first_val = next(iter(sample.values()))
    cases.append(case(dict(sample), _literal_src(first_val),
                      note='normal: %s over sample input' % behavior))
    cases.append(case({n: _zero_for_type(t) for n, t in params} or
                      {'value': ''}, _literal_src(_zero_for_type(
                          params[0][1] if params else 'str')),
                      note='edge: zero/empty input'))
    cases.append(case({first: None}, raises='ValueError',
                      note='invalid: null input raises'))
    return cases


# ---------------------------------------------------------------------------
# Role planners — each returns HIR nodes; plan_project appends them
# ---------------------------------------------------------------------------

def _default_fields():
    return [('id', 'int'), ('name', 'str')]


def _field_type(name):
    low = (name or '').lower()
    if low in ('id',) or low.endswith('_id') or low.endswith('_count'):
        return 'int'
    if low in ('done', 'active', 'enabled') or low.startswith(('is_', 'has_')):
        return 'bool'
    if low in ('price', 'ratio', 'average', 'score_value'):
        return 'float'
    return 'str'


def _normalize_fields(raw):
    if not raw:
        return list(_default_fields())
    try:
        items = list(raw)
    except TypeError:
        return list(_default_fields())
    out = []
    for item in items:
        if isinstance(item, dict):
            fname = item.get('name', '')
            ftype = item.get('type', _field_type(str(fname)))
            out.append((str(fname), str(ftype)))
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            out.append((str(item[0]), str(item[1])))
        else:
            text = str(item)
            out.append((text, _field_type(text)))
    names = [n for n, _t in out if n]
    if not names:
        return list(_default_fields())
    return out


def domain_module(entities, behaviors, package='app', language='python'):
    """Models plus behavior-clustered functions for the domain package.

    Clustering rule: each behavior is assigned to the first entity it names;
    behaviors naming no entity attach to the primary (first) entity. One
    FuncDef is produced per (entity, behavior) pair, and its behaviors list
    always leads with the mapped vocabulary entry plus validation or guard
    entries where the verb family calls for them.
    """
    pkg = _snake(package or 'app')
    lang = str(language or 'python')
    raw_entities = list(entities or [])
    norm_entities = []
    for item in raw_entities:
        if isinstance(item, dict):
            name = item.get('name') or item.get('title') or 'Item'
            fields = _normalize_fields(item.get('fields')
                                       or item.get('attrs') or [])
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            name, fields = item[0], _normalize_fields(item[1])
        else:
            name, fields = str(item), _default_fields()
        code = _code_name(str(name), 'Item')
        norm_entities.append({'code': code, 'snake': _snake(str(name)),
                              'fields': fields})
    if not norm_entities:
        norm_entities.append({'code': 'Item', 'snake': 'item',
                              'fields': list(_default_fields())})
    raw_behaviors = [b for b in (behaviors or []) if str(b).strip()]
    if not raw_behaviors:
        raw_behaviors = ['compute']

    nodes = []
    for ent in norm_entities:
        decls = [H.VarDecl(name=fname, type=H.TypeRef(name=ftype),
                           help='%s field' % fname)
                 for fname, ftype in ent['fields']]
        nodes.append(H.DataModel(name=ent['code'], fields=decls,
                                 doc='%s domain entity.' % ent['code']))

    clusters = []  # (entity_index, verb, behavior)
    for raw in raw_behaviors:
        verb = str(raw).strip().split()[0] if str(raw).strip() else 'compute'
        behavior = _map_verb_to_behavior(verb)
        low = str(raw).lower()
        target = 0
        for i, ent in enumerate(norm_entities):
            if ent['snake'] in re.sub(r'[^a-z0-9]+', '_', low):
                target = i
                break
        clusters.append((target, verb, behavior))

    seen = set()
    funcs = []
    for target, verb, behavior in clusters:
        ent = norm_entities[target]
        fname = '%s_%s' % (_snake(verb), ent['snake'])
        if fname in seen:
            continue
        seen.add(fname)
        params = _params_for_verb(verb, behavior)
        plist = [H.Param(name=pname, type=H.TypeRef(name=ptype),
                         help='%s parameter' % pname)
                 for pname, ptype in params]
        ret = _returns_for(verb, behavior, ent['code'])
        behaviors_list = [_behavior_entry(behavior, ent['code'])]
        if behavior in ('accumulate', 'transform-each', 'filter-where',
                        'compute', 'recurse'):
            behaviors_list.append({'behavior': 'validate-inputs',
                                   'on': ent['code']})
        if behavior in ('search-first', 'count-where', 'none-where',
                        'minmax-loop', 'average'):
            behaviors_list.append({'behavior': 'guard-raise',
                                   'on': ent['code']})
        funcs.append(H.FuncDef(
            name=fname, params=plist,
            returns=H.TypeRef(name=ret) if ret else None,
            doc='%s %s records.' % (verb, ent['code']),
            behaviors=behaviors_list, body=[H.Pass()]))
    nodes.append(H.SourceFile(
        path='%s/domain.py' % pkg, language=lang,
        doc='Domain logic for %s.' % pkg, imports=[],
        declarations=funcs, main_block=[]))
    return nodes


def cli_module(commands, package='app', language='python', entity='Item'):
    """Argparse CLI: one CliCommand per command, handler naming module.func."""
    pkg = _snake(package or 'app')
    lang = str(language or 'python')
    ent_snake = _snake(str(entity or 'Item'))
    wanted = []
    for cmd in commands or []:
        if isinstance(cmd, dict):
            name = cmd.get('name', '')
        else:
            name = str(cmd)
        name = _snake(name) if name else ''
        if name and name not in wanted:
            wanted.append(name)
    for crud in _CRUD_COMMANDS:
        if crud not in wanted:
            wanted.append(crud)
    cli = []
    for name in wanted:
        verb = name.split('_')[0]
        params = _params_for_verb(verb, _map_verb_to_behavior(verb))
        args = [H.Param(name=pname, type=H.TypeRef(name=ptype),
                        help='%s argument' % pname)
                for pname, ptype in params]
        handler = '%s.domain.%s_%s' % (pkg, _snake(verb), ent_snake)
        cli.append(H.CliCommand(name=name, help='Run %s.' % name,
                               args=args, handler=handler))
    main = H.FuncDef(
        name='main', params=[],
        doc='Parse argv and dispatch to the domain handlers.',
        behaviors=[{'behavior': 'branch-return', 'on': 'argv'}],
        body=[H.Pass()])
    cli_file = H.SourceFile(
        path='%s/cli.py' % pkg, language=lang,
        doc='Command-line entry point for %s.' % pkg,
        imports=[H.Import(module='argparse')],
        declarations=[main],
        main_block=[H.ExprStmt(expr=H.Call(func=H.Var(name='main'),
                                          args=[]))])
    return cli + [cli_file]


def _parse_endpoint(item, default_entity):
    method, path = 'GET', '/'
    handler_hint = ''
    if isinstance(item, dict):
        method = str(item.get('method', 'GET')).upper()
        path = str(item.get('path', '/'))
        handler_hint = str(item.get('handler', ''))
    else:
        text = str(item).strip()
        match = re.match(r'(?i)^\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)\s*(.*?)\s*$', text)
        if match:
            method, path, handler_hint = (match.group(1).upper(),
                                          match.group(2),
                                          match.group(3))
        elif text:
            path = text
    if not path.startswith('/'):
        path = '/' + path
    return method, path, handler_hint


def api_module(endpoints, package='app', language='python', entity='Item'):
    """REST-style endpoints plus handlers with (status, body) contracts."""
    pkg = _snake(package or 'app')
    lang = str(language or 'python')
    ent_code = _code_name(str(entity or 'Item'), 'Item')
    ent_snake = _snake(str(entity or 'Item'))
    plural = _plural(ent_snake)
    parsed = []
    for item in endpoints or []:
        parsed.append(_parse_endpoint(item, ent_code))
    if not parsed:
        parsed = [('GET', '/%s' % plural, ''),
                  ('POST', '/%s' % plural, ''),
                  ('GET', '/%s/{id}' % plural, ''),
                  ('PUT', '/%s/{id}' % plural, ''),
                  ('DELETE', '/%s/{id}' % plural, '')]
    nodes = []
    funcs = []
    seen = set()
    for method, path, hint in parsed:
        stem = _snake(hint) if hint else None
        if not stem:
            last = path.rstrip('/').rsplit('/', 1)[-1]
            last = 'one' if last in ('{id}', ':id', '<id>') else last
            stem = '%s_%s' % (method.lower(), _snake(last) or plural)
        if stem in seen:
            continue
        seen.add(stem)
        handler = '%s.api.%s' % (pkg, stem)
        nodes.append(H.ApiEndpoint(
            method=method, path=path, handler=handler,
            request=ent_code, response=ent_code,
            doc='%s %s; handler returns (status, body).' % (method, path)))
        has_id = bool(re.search(r'\{id\}|:id|<id>', path))
        params = [('item_id', 'int')] if has_id else []
        if method in ('POST', 'PUT', 'PATCH'):
            params = list(params) + [('payload', 'dict')]
        if not params:
            params = [('query', 'str')]
        plist = [H.Param(name=p, type=H.TypeRef(name=t),
                         help='%s parameter' % p) for p, t in params]
        funcs.append(H.FuncDef(
            name=stem, params=plist,
            returns=H.TypeRef(name='tuple'),
            doc='Handle %s %s; returns (status, body).' % (method, path),
            behaviors=[{'behavior': 'validate-inputs', 'on': ent_code},
                       {'behavior': 'branch-return', 'on': ent_code}],
            body=[H.Return(value=H.Literal(value=(200, {})))]))
    nodes.append(H.SourceFile(
        path='%s/api.py' % pkg, language=lang,
        doc='HTTP handlers for %s (stdlib http.server).' % pkg,
        imports=[H.Import(module='http.server'), H.Import(module='json')],
        declarations=funcs, main_block=[]))
    return nodes


def store_module(persistence='', package='app', language='python',
                 entities=()):
    """Persistence helpers: JSON file store, or sqlite3 on sql signals."""
    pkg = _snake(package or 'app')
    lang = str(language or 'python')
    text = str(persistence or '').lower()
    if isinstance(persistence, dict):
        text = ' '.join(str(v).lower() for v in persistence.values())
    use_sql = 'sql' in text
    names = []
    for item in entities or []:
        if isinstance(item, dict):
            label = item.get('name') or item.get('title') or 'Item'
        elif isinstance(item, (list, tuple)):
            label = item[0]
        else:
            label = str(item)
        names.append(_snake(str(label)))
    if not names:
        names = ['item']
    imports = [H.Import(module='sqlite3' if use_sql else 'json')]
    if not use_sql:
        imports.append(H.Import(module='pathlib'))
    funcs = []
    for snake in names:
        if use_sql:
            funcs.append(H.FuncDef(
                name='load_%s' % snake,
                params=[H.Param(name='db_path', type=H.TypeRef(name='str'),
                                help='sqlite file path')],
                returns=H.TypeRef(name='list'),
                doc='Load %s rows via sqlite3.' % snake,
                behaviors=[{'behavior': 'guard-raise', 'on': snake},
                           {'behavior': 'branch-return', 'on': snake}],
                body=[H.Pass()]))
            funcs.append(H.FuncDef(
                name='save_%s' % snake,
                params=[H.Param(name='rows', type=H.TypeRef(name='list')),
                        H.Param(name='db_path',
                                type=H.TypeRef(name='str'),
                                help='sqlite file path')],
                returns=H.TypeRef(name='int'),
                doc='Store %s rows via sqlite3.' % snake,
                behaviors=[{'behavior': 'validate-inputs', 'on': snake}],
                body=[H.Pass()]))
        else:
            funcs.append(H.FuncDef(
                name='load_%s' % snake,
                params=[H.Param(name='path', type=H.TypeRef(name='str'),
                                help='json file path')],
                returns=H.TypeRef(name='list'),
                doc='Load %s records from a JSON file.' % snake,
                behaviors=[{'behavior': 'guard-raise', 'on': snake},
                           {'behavior': 'branch-return', 'on': snake}],
                body=[H.Pass()]))
            funcs.append(H.FuncDef(
                name='save_%s' % snake,
                params=[H.Param(name='records',
                                type=H.TypeRef(name='list')),
                        H.Param(name='path', type=H.TypeRef(name='str'),
                                help='json file path')],
                returns=H.TypeRef(name='int'),
                doc='Store %s records to a JSON file.' % snake,
                behaviors=[{'behavior': 'validate-inputs', 'on': snake}],
                body=[H.Pass()]))
    nodes = [H.SourceFile(
        path='%s/store.py' % pkg, language=lang,
        doc='Stdlib persistence for %s.' % pkg,
        imports=imports, declarations=funcs, main_block=[])]
    return nodes


def engine_module(kind, package='app', language='python'):
    """Pure step/advance functions plus a run loop for game/pipeline kinds."""
    pkg = _snake(package or 'app')
    lang = str(language or 'python')
    is_game = str(kind or '').lower() == 'game'
    step = H.FuncDef(
        name='step',
        params=[H.Param(name='state', type=H.TypeRef(name='dict'),
                        help='current state'),
                H.Param(name='action', type=H.TypeRef(name='str'),
                        help='player action' if is_game else 'batch label')],
        returns=H.TypeRef(name='dict'),
        doc='Pure %s transition.' % ('turn' if is_game else 'stage'),
        behaviors=[{'behavior': 'branch-return', 'on': 'state'},
                   {'behavior': 'compute', 'on': 'state'}],
        body=[H.Return(value=H.Var(name='state'))])
    run = H.FuncDef(
        name='run',
        params=[H.Param(name='initial', type=H.TypeRef(name='dict'),
                        help='starting state')],
        returns=H.TypeRef(name='dict'),
        doc='Drive step until done.',
        behaviors=[{'behavior': 'repeat-range', 'on': 'state'},
                   {'behavior': 'branch-return', 'on': 'state'}],
        body=[H.While(
            cond=H.Literal(value=True),
            body=[H.ExprStmt(expr=H.Call(
                func=H.Var(name='step'),
                args=[H.Var(name='initial'),
                      H.Literal(value='tick')])),
                H.Break()])])
    node = H.SourceFile(
        path='%s/engine.py' % pkg, language=lang,
        doc='%s engine for %s.' % ('Game' if is_game else 'Pipeline', pkg),
        imports=[], declarations=[step, run],
        main_block=[H.ExprStmt(expr=H.Call(
            func=H.Var(name='run'), args=[H.DictLit(pairs=[])]))])
    return [node]


def tests_for(project):
    """One TestSpec per function-bearing module; appended to project.tests."""
    specs = []
    for node in project.files:
        if not isinstance(node, H.SourceFile):
            continue
        funcs = [d for d in node.declarations
                 if isinstance(d, H.FuncDef) and d.params]
        if not funcs:
            continue
        dotted = node.path.replace('/', '.')
        if dotted.endswith('.py'):
            dotted = dotted[:-3]
        elif dotted.endswith('.js'):
            dotted = dotted[:-3]
        elif dotted.endswith('.ts'):
            dotted = dotted[:-3]
        for func in funcs:
            first_behavior = 'compute'
            if func.behaviors:
                first = func.behaviors[0]
                if isinstance(first, dict):
                    first_behavior = first.get('behavior', first.get('kind', 'compute'))
                else:
                    first_behavior = str(first)
            verb = func.name.split('_')[0] if func.name else 'compute'
            params = [(p.name, p.type.name if p.type else 'str')
                      for p in func.params]
            fcqh = '%s.%s' % (dotted, func.name)
            fc = []
            for case in _cases_for_func(func.name, verb, params,
                                        first_behavior):
                case.note = '%s: %s' % (func.name, case.note)
                fc.append(case)
            specs.append(H.TestSpec(name='test_%s' % _snake(func.name), target=fcqh, cases=fc,
                                    setup='from %s import *' % dotted.split('.')[0]
                                    if project.language == 'python'
                                    else 'import %s' % dotted))
        name = 'test_%s' % _snake(node.path.rsplit('/', 1)[-1].split('.')[0])
        _ = name  # per-function specs above carry the coverage; no module blob.
    for spec in specs:
        project.tests.append(spec)
    meta = project.meta
    decisions = meta.setdefault('decisions', [])
    decisions.append({'kind': 'decision', 'choice': 'tests',
                      'value': '%d specs' % len(specs),
                      'reason': 'one TestSpec per function-bearing module'})
    return specs


def config_files(language, package='app', title='app'):
    """Packaging manifest, ignore file and README for the given language."""
    lang = str(language or 'python').lower()
    pkg = _snake(package or 'app')
    label = str(title or pkg)
    files = []
    if lang in ('javascript', 'typescript'):
        files.append(H.ConfigFile(
            path='package.json', format='json',
            data={'name': pkg, 'version': '0.1.0',
                  'description': label, 'type': 'module',
                  'scripts': {'test': 'node --test'}},
            comment='Package manifest for %s.' % label))
        files.append(H.ConfigFile(
            path='manifest.json', format='json',
            data={'name': pkg, 'version': '0.1.0',
                  'files': ['%s/' % pkg, 'README.md']},
            comment='File manifest for %s.' % label))
    else:
        files.append(H.ConfigFile(
            path='pyproject.toml', format='toml',
            data={'project': {'name': pkg, 'version': '0.1.0',
                              'description': label,
                              'requires-python': '>=3.11'},
                  'build-system': {'requires': ['setuptools>=61'],
                                   'build-backend':
                                   'setuptools.build_meta'}},
            comment='Build config for %s.' % label))
        files.append(H.ConfigFile(
            path='MANIFEST.in', format='txt',
            data='include README.md\nrecursive-include %s *.py\n' % pkg,
            comment='Source manifest for %s.' % label))
    files.append(H.ConfigFile(
        path='.gitignore', format='txt',
        data='__pycache__/\n*.pyc\nnode_modules/\ndist/\n.env\n',
        comment='Ignore build output and local env.'))
    files.append(H.DocFile(
        path='README.md', title=label,
        sections=[('Overview', '%s. Planned by Epsilon.' % label),
                  ('Install', 'Python: pip install -e . '
                   'JS: npm install. No third-party deps.'),
                  ('Usage', 'Import the package modules or run the '
                   'entry point described in project meta.'),
                  ('Testing', 'Python: pytest -q. '
                   'JS: node --test. Specs live in project tests.')]))
    return files


# ---------------------------------------------------------------------------
# Project composer
# ---------------------------------------------------------------------------

def plan_project(req, language: str, events=None) -> H.Project:
    """Compose the role planners into one HIR project for req."""
    title = _title_of(req)
    desc = _description_of(req)
    lang, lang_note = _normalize_language(language)
    ptype, scores, matched, fell_back = _detection_detail(req)
    max_files = _max_files_of(req)

    raw_entities = _entities_of(req)
    entities = [{'name': name, 'fields': fields}
                for name, fields in raw_entities]
    if not entities:
        first = re.findall(r'[A-Za-z0-9]+', title)
        fallback = first[0] if first else 'Item'
        entities = [{'name': fallback, 'fields': []}]
    pkg = _project_slug(title, entities)

    project = H.Project(name=title, description=desc, language=lang,
                        meta={'project-type': ptype, 'package': pkg,
                              'max_files': max_files, 'decisions': []})
    decide, assume = _recorder(project.meta, events)

    decide('project-type', ptype,
           'score=%d signals=%s' % (scores[ptype], matched[ptype] or ['none']))
    if fell_back:
        assume('project-type-fallback', 'library',
               'fewer than 2 supporting signals; generic package plan')
    if lang_note:
        assume('language', lang, lang_note)
    else:
        decide('language', lang, 'normalized from %r' % (language,))
    decide('package-dir', pkg, 'sanitized from title %r' % title)
    decide('max-files', str(max_files), 'recorded; enforced downstream')

    raw_entities = _entities_of(req)
    entities = [{'name': name, 'fields': fields}
                for name, fields in raw_entities]
    if not entities:
        first = re.findall(r'[A-Za-z0-9]+', title)
        fallback = first[0] if first else 'Item'
        entities = [{'name': fallback, 'fields': []}]
        assume('default-entity', _code_name(fallback),
               'no entities given; derived from title')
    behaviors = [b for b in _behaviors_of(req) if str(b).strip()]
    if not behaviors:
        behaviors = ['compute']
        assume('default-behavior', 'compute', 'no behaviors given')
    primary = entities[0]['name']

    for node in domain_module(entities, behaviors, pkg, lang):
        _append_node(project, node)
    decide('domain-module', '%s/domain.py' % pkg,
           '%d models, behavior clusters by entity'
           % len(project.models))

    interfaces = _interfaces_of(req)
    persistence = _persistence_text(req)
    needs_store = bool(persistence.strip()) or ptype in ('db', 'service')

    if ptype in ('cli', 'tool'):
        cli_cmds = interfaces.get('cli', [])
        if isinstance(cli_cmds, str):
            cli_cmds = [cli_cmds]
        try:
            cli_cmds = list(cli_cmds) if not isinstance(cli_cmds, dict) \
                else [cli_cmds.get('command', '')]
        except TypeError:
            cli_cmds = []
        for node in cli_module(cli_cmds, pkg, lang, primary):
            _append_node(project, node)
        decide('cli-module', '%s/cli.py' % pkg,
               '%d commands incl. CRUD verbs' % len(project.cli))
    if ptype in ('api', 'service', 'bot', 'frontend'):
        eps = _endpoints_of(req)
        for node in api_module(eps, pkg, lang, primary):
            _append_node(project, node)
        decide('api-module', '%s/api.py' % pkg,
               '%d endpoints; stdlib http.server only' % len(project.endpoints))
    if needs_store:
        for node in store_module(persistence or 'json file', pkg, lang,
                                 [e['name'] for e in entities]):
            _append_node(project, node)
        backend = 'sqlite3' if 'sql' in persistence.lower() else 'json file'
        decide('store-module', '%s/store.py' % pkg,
               'stdlib-only backend: %s' % backend)
    if ptype in ('game', 'pipeline'):
        for node in engine_module(ptype, pkg, lang):
            _append_node(project, node)
        decide('engine-module', '%s/engine.py' % pkg,
               'pure step plus run loop for %s' % ptype)

    specs = tests_for(project)
    _emit(events, 'plan', 'decided', 'tests -> %d specs' % len(specs),
          {'choice': 'tests', 'value': '%d specs' % len(specs)})
    for node in config_files(lang, pkg, title):
        _append_node(project, node)
    decide('config-files', 'manifest+ignore+README',
           'packaging for %s' % lang)

    if ptype in ('cli', 'tool'):
        entry = '%s/cli.py' % pkg
    elif ptype in ('api', 'service', 'bot', 'frontend'):
        entry = '%s/api.py' % pkg
    elif ptype in ('game', 'pipeline'):
        entry = '%s/engine.py' % pkg
    elif ptype == 'db':
        entry = '%s/store.py' % pkg
    else:
        entry = '%s/domain.py' % pkg
    project.entry = entry
    decide('entry', entry, 'main runnable for type %s' % ptype)
    return project


def plan_function_task(name, params, behaviors, language) -> H.Project:
    """Level fast path: single-module library project, one function + tests."""
    lang, lang_note = _normalize_language(language)
    func_name = _code_name(name, 'do_task')
    mod_snake = _snake(name, 'task')
    pkg = _package_dir(name or 'task')
    raw_behaviors = [str(b).strip() for b in (behaviors or [])
                     if str(b).strip()]
    mapped = [_map_verb_to_behavior(b.split()[0]) for b in raw_behaviors]
    if not mapped:
        mapped = ['compute']
    vocab = _vocabulary()
    behaviors_list = [{'behavior': b if b in vocab else 'compute'}
                      for b in mapped]
    plist = []
    for item in params or []:
        if isinstance(item, dict):
            pname = str(item.get('name', 'value'))
            ptype = str(item.get('type', _type_for_param(pname)))
        else:
            pname = str(item)
            ptype = _type_for_param(pname)
        pname = _code_name(pname, 'value')
        plist.append(H.Param(name=pname, type=H.TypeRef(name=ptype),
                             help='%s parameter' % pname))
    if not plist:
        plist = [H.Param(name='value', type=H.TypeRef(name='str'),
                         help='value parameter')]
    first_verb = raw_behaviors[0].split()[0] if raw_behaviors else 'compute'
    ret = _returns_for(first_verb, mapped[0], func_name)
    func = H.FuncDef(
        name=func_name, params=plist,
        returns=H.TypeRef(name=ret) if ret else None,
        doc='%s task function.' % func_name,
        behaviors=behaviors_list, body=[H.Pass()])
    ptype = 'algorithmic' if 'recurse' in mapped else 'library'
    project = H.Project(
        name=str(name or 'task'), description='Single-function %s task.'
        % func_name, language=lang,
        meta={'project-type': ptype, 'package': pkg,
              'max_files': _DEFAULT_MAX_FILES, 'decisions': []})
    decide, assume = _recorder(project.meta, None)
    decide('project-type', ptype,
           'function fast path; recurse=%s' % ('recurse' in mapped))
    if lang_note:
        assume('language', lang, lang_note)
    if not raw_behaviors:
        assume('default-behavior', 'compute', 'no behaviors given')
    project.files.append(H.SourceFile(
        path='%s/%s.py' % (pkg, mod_snake), language=lang,
        doc='Single-function module for %s.' % func_name,
        imports=[], declarations=[func], main_block=[]))
    decide('function-module', '%s/%s.py' % (pkg, mod_snake),
           'one function with %d behaviors' % len(behaviors_list))
    specs = tests_for(project)
    _ = specs
    for node in config_files(lang, pkg, str(name or 'task')):
        _append_node(project, node)
    decide('entry', '%s/%s.py' % (pkg, mod_snake), 'single module project')
    project.entry = '%s/%s.py' % (pkg, mod_snake)
    return project
