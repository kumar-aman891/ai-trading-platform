"""Per-rule evaluation vocabulary.

RuleOutcome is intentionally three-valued, not boolean - INDETERMINATE is
distinct from REJECT ("we don't know" vs "we know, and the answer is no").
The aggregator in risk.engine collapses INDETERMINATE to an overall
REJECTED decision (reject-by-default, rules/02-live-trading.md), but the
per-rule record preserves the distinction for audit purposes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class RuleOutcome(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True, slots=True)
class RuleResult:
    rule_id: str
    outcome: RuleOutcome
    message: str
    evidence: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not self.rule_id.strip():
            raise ValueError("rule_id must not be empty.")
        if not isinstance(self.evidence, MappingProxyType):
            object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
