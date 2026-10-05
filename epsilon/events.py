"""Epsilon v2 observability. Structured stage events; silent unless asked."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Event:
    seq: int
    stage: str       # parse | plan | generate | validate | execute | repair | ...
    kind: str        # started | decided | produced | failed | repaired | ...
    message: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {'seq': self.seq, 'stage': self.stage, 'kind': self.kind,
                'message': self.message, 'data': self.data}


class EventLog:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self._n = 0

    def emit(self, stage: str, kind: str, message: str, data: dict | None = None) -> Event:
        self._n += 1
        ev = Event(seq=self._n, stage=stage, kind=kind, message=message, data=dict(data or {}))
        self.events.append(ev)
        return ev

    def for_stage(self, stage: str) -> list[Event]:
        return [e for e in self.events if e.stage == stage]

    def failures(self) -> list[Event]:
        return [e for e in self.events if e.kind in ('failed', 'error')]

    def summary(self) -> list[str]:
        return ['%d [%s/%s] %s' % (e.seq, e.stage, e.kind, e.message) for e in self.events]

    def to_dict(self) -> dict:
        return {'events': [e.to_dict() for e in self.events]}
