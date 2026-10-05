"""Epsilon v2 — general symbolic code-generation engine.

Native path: requirements.analyze -> planner.plan_project -> pipeline.build
(multi-file projects with validation, tests, repair). The v1 single-function
API is preserved via compat_v1 (legacy adapter, not core architecture).
"""
from .errors import (PASS, FAIL, UNKNOWN, UNAVAILABLE, EpsilonError, Verdict,
                     ProjectReport, err, warn)
from .config import EpsilonConfig
from .events import EventLog, Event
from . import hir
from . import expr as exprlang
from . import validate
from . import requirements
from . import planner
from . import pipeline
from . import sandbox
from . import repair
from . import backends
from . import snippets
from . import testgen
from . import context
from . import deps
from . import compat_v1

verify = validate  # legacy alias: `from epsilon import verify` keeps working

__version__ = '2.0.0'


def build_project(spec, language='python', out_dir='dist', **options):
    """Native v2 entry: spec text -> verified multi-file project on disk."""
    from .pipeline import build
    config = EpsilonConfig(target_language=language, out_dir=out_dir,
                           **{k: v for k, v in options.items()
                               if k in EpsilonConfig.__dataclass_fields__})
    events = EventLog()
    return build(spec, config, events)


# ---- v1 compatibility (legacy adapter) ----

def generate(spec, lang='python', rank=True):
    from .compat_v1 import generate as _gen
    return _gen(spec, lang=lang)


def verify_code(code, lang='python'):
    from . import validate as V
    language = (lang or 'python').lower()
    verdicts = V.validate_file_content(code, language)
    syntax_ok = all(v.state == PASS for v in verdicts)
    return {'lang': language, 'syntax_ok': bool(syntax_ok), 'exec_ok': False,
            'exec_state': 'UNKNOWN',
            'errors': [e.to_dict() if hasattr(e, 'to_dict') else e
                       for v in verdicts for e in v.errors]}
