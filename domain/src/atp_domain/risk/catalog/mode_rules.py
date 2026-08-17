"""MODE family - two real PAPER implementations, plus the LIVE stub
completing the canonical ID set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from atp_domain.killswitch import SwitchId, SwitchScope, is_blocking, resolve_switch_state
from atp_domain.proposals import TradeProposal
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.rule import RuleContext

RULE_ID_MODE_MATCHES_CONFIG = "RISK.MODE.001"
RULE_ID_KILL_SWITCH = "RISK.MODE.002"
RULE_ID_LIVE_ACTIVATION = "RISK.MODE.003"


@dataclass(frozen=True, slots=True)
class ModeMatchesConfigRule:
    """A proposal must be evaluated against a risk config of the same mode
    - prevents a caller bug from checking a PAPER proposal against a LIVE
    config or vice versa."""

    rule_id: ClassVar[str] = RULE_ID_MODE_MATCHES_CONFIG

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        if proposal.mode is context.config.mode:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.PASS,
                message="Proposal mode matches risk config mode.",
            )
        return RuleResult(
            rule_id=self.rule_id,
            outcome=RuleOutcome.REJECT,
            message=(
                f"Proposal mode {proposal.mode} does not match risk config "
                f"mode {context.config.mode}."
            ),
        )


@dataclass(frozen=True, slots=True)
class PaperKillSwitchRule:
    """Fail-closed: an unreadable or engaged PAPER kill switch blocks."""

    rule_id: ClassVar[str] = RULE_ID_KILL_SWITCH

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        switch_id = SwitchId(SwitchScope.PAPER)
        state = resolve_switch_state(switch_id, context.kill_switch_states)
        if is_blocking(state):
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.REJECT,
                message=f"PAPER kill switch is {state.value}.",
                evidence={"switch_state": state.value},
            )
        return RuleResult(
            rule_id=self.rule_id,
            outcome=RuleOutcome.PASS,
            message="PAPER kill switch is disengaged.",
        )


__all__ = [
    "RULE_ID_KILL_SWITCH",
    "RULE_ID_LIVE_ACTIVATION",
    "RULE_ID_MODE_MATCHES_CONFIG",
    "ModeMatchesConfigRule",
    "PaperKillSwitchRule",
]
