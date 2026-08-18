"""`atp_exec_paper.simulator` - the deliberately fake fill simulator."""

from __future__ import annotations

import pytest

from atp_domain.types import FillId, OrderId, OrderType
from atp_exec_paper.simulator import (
    SOURCE_PAPER_SIMULATOR,
    MarketOrderNotSimulatableError,
    simulate_fill,
)
from tests.unit.exec_paper.builders import NOW, make_proposal


def test_limit_proposal_fills_immediately_at_limit_price() -> None:
    proposal = make_proposal(limit_price="123.45", quantity="7")
    fill = simulate_fill(
        proposal,
        fill_id=FillId("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"),
        internal_order_id=OrderId("bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb"),
        filled_at=NOW,
    )

    assert fill.simulated is True
    assert fill.price.value == proposal.limit_price.value  # type: ignore[union-attr]
    assert fill.quantity.value == proposal.quantity.value
    assert fill.fees.value == 0
    assert fill.taxes.value == 0


def test_market_proposal_raises_instead_of_inventing_a_price() -> None:
    proposal = make_proposal(order_type=OrderType.MARKET, limit_price=None)
    with pytest.raises(MarketOrderNotSimulatableError):
        simulate_fill(
            proposal,
            fill_id=FillId("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"),
            internal_order_id=OrderId("bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb"),
            filled_at=NOW,
        )


def test_source_constant_matches_documented_value() -> None:
    assert SOURCE_PAPER_SIMULATOR == "PAPER_SIMULATOR"
