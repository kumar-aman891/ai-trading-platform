"""The deliberately fake fill simulator (docs/schemas/fill.md).

For a LIMIT proposal: immediate full fill at `proposal.limit_price`. No
slippage, no partial fills, no latency, no fees, no taxes
(`fees = taxes = 0`). Every `Fill` produced here carries
`simulated = True`; the caller supplies `source = SOURCE_PAPER_SIMULATOR`
when persisting it (`atp_persistence.repositories.fills`).

A MARKET proposal is never passed here - the risk engine rejects it first
(`RISK.DATA.001`/`RISK.CAPITAL.001`/`RISK.LIMIT.001` are all INDETERMINATE
without a priced reference, Step 9 D3; ADR-011). `simulate_fill` asserts
this explicitly rather than silently inventing a price if that invariant is
ever violated by a future caller.
"""

from __future__ import annotations

from datetime import datetime

from atp_domain.money import Money
from atp_domain.orders import Fill
from atp_domain.proposals import TradeProposal
from atp_domain.types import FillId, OrderId, OrderType

SOURCE_PAPER_SIMULATOR = "PAPER_SIMULATOR"


class MarketOrderNotSimulatableError(ValueError):
    """Raised if the simulator is ever called with a MARKET proposal -
    should be unreachable in practice, since an APPROVED PAPER decision for
    a MARKET proposal is impossible (D3), but asserted explicitly rather
    than silently inventing a fill price."""


def simulate_fill(
    proposal: TradeProposal,
    *,
    fill_id: FillId,
    internal_order_id: OrderId,
    filled_at: datetime,
) -> Fill:
    if proposal.order_type is OrderType.MARKET or proposal.limit_price is None:
        raise MarketOrderNotSimulatableError(
            "The paper simulator never fills a MARKET order - no canonical "
            "price source exists in Phase 1 (Step 9 D3); this proposal "
            "should have been rejected by the risk engine before reaching "
            "the simulator."
        )
    return Fill(
        fill_id=fill_id,
        mode=proposal.mode,
        internal_order_id=internal_order_id,
        quantity=proposal.quantity,
        price=proposal.limit_price,
        fees=Money.zero(),
        taxes=Money.zero(),
        simulated=True,
        filled_at=filled_at,
    )
