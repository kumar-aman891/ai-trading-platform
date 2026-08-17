"""LIMIT family - one real PAPER implementation (maximum order notional),
plus six LIVE-only stubs completing the canonical ID set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from atp_domain.proposals import TradeProposal
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.rule import RuleContext
from atp_domain.types import OrderType

RULE_ID_MAX_ORDER_NOTIONAL = "RISK.LIMIT.001"
RULE_ID_PER_TRADE_MAX_LOSS = "RISK.LIMIT.002"
RULE_ID_DAILY_LOSS_LIMIT = "RISK.LIMIT.003"
RULE_ID_CONCENTRATION = "RISK.LIMIT.004"
RULE_ID_SECTOR_CONCENTRATION = "RISK.LIMIT.005"
RULE_ID_STRATEGY_ALLOCATION = "RISK.LIMIT.006"
RULE_ID_POSITION_LIMITS = "RISK.LIMIT.007"


@dataclass(frozen=True, slots=True)
class MaxOrderNotionalRule:
    """INDETERMINATE for MARKET orders (no price to compute notional
    against)."""

    rule_id: ClassVar[str] = RULE_ID_MAX_ORDER_NOTIONAL

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        if proposal.order_type is OrderType.MARKET or proposal.limit_price is None:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.INDETERMINATE,
                message="Cannot compute notional for a MARKET order without a reference price.",
            )

        notional = proposal.quantity * proposal.limit_price
        max_notional = context.config.max_order_notional
        if notional.value > max_notional.value:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.REJECT,
                message=f"Notional {notional} exceeds configured max_order_notional {max_notional}.",
            )
        return RuleResult(
            rule_id=self.rule_id,
            outcome=RuleOutcome.PASS,
            message="Notional is within the configured maximum.",
        )


__all__ = [
    "RULE_ID_CONCENTRATION",
    "RULE_ID_DAILY_LOSS_LIMIT",
    "RULE_ID_MAX_ORDER_NOTIONAL",
    "RULE_ID_PER_TRADE_MAX_LOSS",
    "RULE_ID_POSITION_LIMITS",
    "RULE_ID_SECTOR_CONCENTRATION",
    "RULE_ID_STRATEGY_ALLOCATION",
    "MaxOrderNotionalRule",
]
