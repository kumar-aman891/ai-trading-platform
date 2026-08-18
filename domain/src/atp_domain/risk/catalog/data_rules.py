"""DATA family - RISK.DATA.001 has a real, deliberately narrow Phase 1 PAPER
implementation (below); the other four canonical IDs remain LIVE-only stubs
(no market-data/news adapter exists yet).

RISK.DATA.001's full canonical description is "price sanity vs latest
canonical quote," which would require a canonical quote source Phase 1 does
not have (no market-data adapter - docs/DATA_SOURCES.md). What the real
Phase 1 implementation checks is narrower: does this proposal carry a
priced reference at all. This is enough to make "a PAPER MARKET proposal
must deterministically reject, with no invented reference price" an
explicit, named, audited rule result (Step 9 D3) rather than an implicit
side effect of RISK.CAPITAL.001/RISK.LIMIT.001 also being unable to price a
MARKET order (both remain INDETERMINATE for MARKET independently - this
rule does not replace that behavior, it makes the reason explicit)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from atp_domain.proposals import TradeProposal
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.rule import RuleContext
from atp_domain.types import OrderType

RULE_ID_PRICED_REFERENCE = "RISK.DATA.001"

CANONICAL_RULES: tuple[tuple[str, str], ...] = (
    (RULE_ID_PRICED_REFERENCE, "Price sanity vs latest canonical quote"),
    ("RISK.DATA.002", "Data freshness threshold"),
    ("RISK.DATA.003", "Spread/liquidity threshold"),
    ("RISK.DATA.004", "Circuit/price-band sanity"),
    ("RISK.DATA.005", "News/event blackout rules"),
)


@dataclass(frozen=True, slots=True)
class PricedReferenceRule:
    """PASS if the proposal carries its own priced reference (a LIMIT
    order's `limit_price` - no canonical quote lookup is needed to
    evaluate one). INDETERMINATE for MARKET orders - no Phase 1 source can
    supply a reference price, and the engine's reject-by-default
    aggregation turns INDETERMINATE into REJECTED (rules/02-live-trading.md:
    "when any hard check is indeterminate, reject... rather than
    guessing")."""

    rule_id: ClassVar[str] = RULE_ID_PRICED_REFERENCE

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        if proposal.order_type is OrderType.MARKET or proposal.limit_price is None:
            return RuleResult(
                rule_id=self.rule_id,
                outcome=RuleOutcome.INDETERMINATE,
                message=(
                    "No canonical quote source exists in Phase 1 - a MARKET "
                    "order cannot be priced and is rejected deterministically "
                    "rather than filled at an invented reference price."
                ),
            )
        return RuleResult(
            rule_id=self.rule_id,
            outcome=RuleOutcome.PASS,
            message=(
                "Proposal carries its own priced reference (limit_price); no "
                "canonical quote lookup is required to evaluate it."
            ),
        )


__all__ = ["CANONICAL_RULES", "RULE_ID_PRICED_REFERENCE", "PricedReferenceRule"]
