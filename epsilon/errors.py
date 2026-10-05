"""Epsilon v2 structured error model. Replaces scattered strings/exceptions."""
from __future__ import annotations

from dataclasses import dataclass, field

# Verdict states — never claim more than was actually verified.
PASS = 'PASS'
FAIL = 'FAIL'
UNKNOWN = 'UNKNOWN'
UNAVAILABLE = 'UNAVAILABLE'
STATES = (PASS, FAIL, UNKNOWN, UNAVAILABLE)

# Error categories.
SYNTAX = 'syntax'
AST_VALIDITY = 'ast'
IMPORTS = 'imports'
SYMBOLS = 'symbols'
TYPES = 'types'
STRUCTURE = 'structure'
DEPS = 'dependencies'
TESTS = 'tests'
RUNTIME = 'runtime'
REPAIR = 'repair'
CONFIG = 'config'
PLANNING = 'planning'
GENERATION = 'generation'
VALIDATION = 'validation'
EXECUTION = 'execution'

# Severities.
ERROR = 'error'
WARNING = 'warning'
INFO = 'info'


@dataclass
class EpsilonError:
    category: str
    severity: str
    message: str
    source_file: str = ''
    line: int | None = None
    col: int | None = None
    symbol: str = ''
    probable_cause: str = ''
    suggested_repair: str = ''

    def to_dict(self) -> dict:
        return {
            'category': self.category, 'severity': self.severity,
            'message': self.message, 'source_file': self.source_file,
            'line': self.line, 'col': self.col, 'symbol': self.symbol,
            'probable_cause': self.probable_cause,
            'suggested_repair': self.suggested_repair,
        }

    def __str__(self) -> str:
        loc = self.source_file
        if self.line is not None:
            loc += ':%s' % self.line
            if self.col is not None:
                loc += ':%s' % self.col
        head = '[%s/%s]' % (self.category, self.severity)
        return ('%s %s %s' % (head, loc, self.message)).strip()


def err(category: str, message: str, severity: str = ERROR, **kw) -> EpsilonError:
    return EpsilonError(category=category, severity=severity, message=message, **kw)


def warn(category: str, message: str, **kw) -> EpsilonError:
    return EpsilonError(category=category, severity=WARNING, message=message, **kw)


@dataclass
class Verdict:
    """One validation layer's outcome. state is one of PASS/FAIL/UNKNOWN/UNAVAILABLE."""
    layer: str
    state: str
    errors: list = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def ok(self) -> bool:
        return self.state == PASS

    def to_dict(self) -> dict:
        return {'layer': self.layer, 'state': self.state,
                'errors': [e.to_dict() if isinstance(e, EpsilonError) else e for e in self.errors],
                'details': self.details}


@dataclass
class ProjectReport:
    state: str
    verdicts: list = field(default_factory=list)
    tests: dict = field(default_factory=dict)
    repairs: list = field(default_factory=list)
    files: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'state': self.state,
            'verdicts': [v.to_dict() if isinstance(v, Verdict) else v for v in self.verdicts],
            'tests': self.tests, 'repairs': self.repairs,
            'files': self.files, 'metrics': self.metrics,
        }
