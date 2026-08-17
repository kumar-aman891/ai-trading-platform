"""CAPITAL family - one real PAPER implementation (simulated cash
sufficiency), plus the margin-sufficiency LIVE stub completing the
canonical ID set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from atp_domain.proposals import TradeProposal
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.rule import RuleContext
from atp_domain.types import OrderType

RULE_ID_SIMULATED_CASH = "RISK.CAPITAL.001"
RULE_ID_MARGIN = "RISK.CAPITAL.002"


@dataclass(frozen=True, slots=True)
class SimulatedCashSufficiencyRule:
    """INDETERMINATE if available_cash wasn't supplied, or if the order is
    MARKET-priced (no price to compute notional against, and Phase 1 has
    no market data adapter to supply a reference price)."""

    rule_id: ClassVar[str] = RULE_ID_SIMULATED_CASH

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        if context.available_cash is None:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.INDETERMINATE,
                message="available_cash was not supplied in RuleContext.",
            )
        if proposal.order_type is OrderType.MARKET or proposal.limit_price is None:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.INDETERMINATE,
                message="Cannot compute notional for a MARKET order without a reference price.",
            )

        notional = proposal.quantity * proposal.limit_price
        if notional.value > context.available_cash.value:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.REJECT,
                message=f"Notional {notional} exceeds available simulated cash {context.available_cash}.",
            )
        return RuleResult(
            rule_id=self.rule_id,
            outcome=RuleOutcome.PASS,
            message="Sufficient simulated cash for this proposal's notional.",
        )


__all__ = ["RULE_ID_MARGIN", "RULE_ID_SIMULATED_CASH", "SimulatedCashSufficiencyRule"]
