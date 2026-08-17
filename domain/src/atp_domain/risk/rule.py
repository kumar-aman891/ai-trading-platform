"""Rule protocol and RuleContext.

RuleContext carries every caller-supplied fact a rule might need. The
domain never fetches these itself - populating this is the caller's
(eventually the executor's) responsibility, which is what keeps the risk
engine free of I/O (rules/01-architecture.md). A missing fact is
represented as `None`, and a rule that needs a fact it wasn't given returns
INDETERMINATE rather than guessing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from atp_domain.killswitch import SwitchId, SwitchState
from atp_domain.money import Money, Price
from atp_domain.proposals import TradeProposal
from atp_domain.risk.config import RiskConfig
from atp_domain.risk.outcomes import RuleResult


@dataclass(frozen=True, slots=True)
class RuleContext:
    config: RiskConfig
    kill_switch_states: Mapping[SwitchId, SwitchState] = field(
        default_factory=lambda: MappingProxyType({})
    )
    available_cash: Money | None = None
    instrument_lot_size: int | None = None
    instrument_tick_size: Price | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kill_switch_states, MappingProxyType):
            object.__setattr__(
                self, "kill_switch_states", MappingProxyType(dict(self.kill_switch_states))
            )


@runtime_checkable
class Rule(Protocol):
    @property
    def rule_id(self) -> str: ...

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult: ...
