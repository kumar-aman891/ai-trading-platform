"""A single reusable stub rule, used for every canonical rule ID that has
no real Phase 1 implementation - always INDETERMINATE, regardless of input.

Used for all 28 canonical rule IDs under LIVE (guaranteeing LIVE can never
reach APPROVED - INDETERMINATE always collapses to REJECT, see
risk.engine), and for the 22 canonical IDs that have no real PAPER
implementation either.
"""

from __future__ import annotations

from dataclasses import dataclass

from atp_domain.proposals import TradeProposal
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.rule import RuleContext


@dataclass(frozen=True, slots=True)
class NotImplementedRule:
    rule_id: str
    description: str

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        return RuleResult(
            rule_id=self.rule_id,
            outcome=RuleOutcome.INDETERMINATE,
            message=f"{self.description} - not implemented in Phase 1.",
        )
