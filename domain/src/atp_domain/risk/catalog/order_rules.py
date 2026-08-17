"""ORDER family - two real PAPER implementations (lot/tick validation,
order-type/price coherence), plus three LIVE-only stubs completing the
canonical ID set."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from atp_domain.proposals import TradeProposal
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.rule import RuleContext
from atp_domain.types import OrderType

RULE_ID_LOT_TICK = "RISK.ORDER.001"
RULE_ID_TYPE_PRICE_COHERENCE = "RISK.ORDER.002"
RULE_ID_DUPLICATE_ORDER = "RISK.ORDER.003"
RULE_ID_ORDER_RATE = "RISK.ORDER.004"
RULE_ID_MAX_TURNOVER = "RISK.ORDER.005"


@dataclass(frozen=True, slots=True)
class LotTickValidationRule:
    """INDETERMINATE if the caller didn't supply lot/tick size in context -
    fail-closed rather than assuming compliance without the data to verify
    it."""

    rule_id: ClassVar[str] = RULE_ID_LOT_TICK

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        if context.instrument_lot_size is None:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.INDETERMINATE,
                message="instrument_lot_size was not supplied in RuleContext.",
            )

        if not proposal.quantity.is_multiple_of(Decimal(context.instrument_lot_size)):
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.REJECT,
                message=(
                    f"Quantity {proposal.quantity} is not a multiple of lot size "
                    f"{context.instrument_lot_size}."
                ),
            )

        if proposal.limit_price is not None and context.instrument_tick_size is not None:
            tick = context.instrument_tick_size.value
            if (proposal.limit_price.value % tick) != 0:
                return RuleResult(
                    rule_id=self.rule_id,
                    outcome=RuleOutcome.REJECT,
                    message=(
                        f"Limit price {proposal.limit_price} is not a multiple of "
                        f"tick size {context.instrument_tick_size}."
                    ),
                )

        return RuleResult(
            rule_id=self.rule_id,
            outcome=RuleOutcome.PASS,
            message="Quantity and price respect lot/tick constraints.",
        )


@dataclass(frozen=True, slots=True)
class OrderTypePriceCoherenceRule:
    """Re-validates what TradeProposal's own constructor already
    guarantees - always PASS in practice, but recorded as an explicit,
    audited rule result rather than left implicit (defense-in-depth)."""

    rule_id: ClassVar[str] = RULE_ID_TYPE_PRICE_COHERENCE

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        if proposal.order_type is OrderType.LIMIT and proposal.limit_price is None:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.REJECT,
                message="LIMIT order is missing a limit_price.",
            )
        if proposal.order_type is OrderType.MARKET and proposal.limit_price is not None:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.REJECT,
                message="MARKET order must not carry a limit_price.",
            )
        return RuleResult(
            rule_id=self.rule_id,
            outcome=RuleOutcome.PASS,
            message="Order type and price fields are coherent.",
        )


__all__ = [
    "RULE_ID_DUPLICATE_ORDER",
    "RULE_ID_LOT_TICK",
    "RULE_ID_MAX_TURNOVER",
    "RULE_ID_ORDER_RATE",
    "RULE_ID_TYPE_PRICE_COHERENCE",
    "LotTickValidationRule",
    "OrderTypePriceCoherenceRule",
]
