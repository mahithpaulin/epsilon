"""Epsilon v2 configuration. Explicit knobs, validated, serializable."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EpsilonConfig:
    target_language: str = 'python'          # python | javascript | typescript
    out_dir: str = 'dist'
    validation_level: str = 'standard'       # minimal | standard | strict
    run_tests: bool = True
    repair_iterations: int = 3
    dependency_policy: str = 'stdlib-first'  # stdlib-first | allow-third-party | frozen
    sandbox_policy: str = 'restricted'       # restricted | open
    verbosity: int = 0                       # 0 quiet, 1 stages, 2 everything
    deterministic: bool = True
    timeout_secs: float = 30.0
    max_files: int = 40
    max_repair_files: int = 5

    def validate(self) -> list[str]:
        problems = []
        if self.target_language not in ('python', 'javascript', 'typescript'):
            problems.append('target_language must be python|javascript|typescript')
        if self.validation_level not in ('minimal', 'standard', 'strict'):
            problems.append('validation_level must be minimal|standard|strict')
        if self.dependency_policy not in ('stdlib-first', 'allow-third-party', 'frozen'):
            problems.append('dependency_policy must be stdlib-first|allow-third-party|frozen')
        if self.sandbox_policy not in ('restricted', 'open'):
            problems.append('sandbox_policy must be restricted|open')
        if self.repair_iterations < 0 or self.repair_iterations > 10:
            problems.append('repair_iterations must be 0..10')
        if self.timeout_secs <= 0 or self.timeout_secs > 600:
            problems.append('timeout_secs must be 0..600')
        return problems

    def to_dict(self) -> dict:
        return {f: getattr(self, f) for f in (
            'target_language', 'out_dir', 'validation_level', 'run_tests',
            'repair_iterations', 'dependency_policy', 'sandbox_policy',
            'verbosity', 'deterministic', 'timeout_secs',
            'max_files', 'max_repair_files')}

    @classmethod
    def from_dict(cls, d: dict) -> 'EpsilonConfig':
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})
