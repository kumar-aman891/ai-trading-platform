"""Tests for atp_domain.proposals.TradeProposal - immutability, mode
non-null, tz-aware created_at, order/price coherence."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atp_domain.errors import InvalidTradeProposalError
from atp_domain.money import Price, Quantity
from atp_domain.proposals import TradeProposal
from atp_domain.types import InstrumentId, Mode, OrderType, Product, ProposalId, Side

_DEFAULT_LIMIT_PRICE = Price(Decimal("100"))


def _make(
    *,
    mode: Mode = Mode.PAPER,
    order_type: OrderType = OrderType.LIMIT,
    limit_price: Price | None = _DEFAULT_LIMIT_PRICE,
    created_at: datetime | None = None,
    client_request_id: str = "req-1",
) -> TradeProposal:
    return TradeProposal(
        proposal_id=ProposalId("11111111-1111-7111-8111-111111111111"),
        mode=mode,
        instrument_id=InstrumentId("22222222-2222-7222-8222-222222222222"),
        side=Side.BUY,
        quantity=Quantity(Decimal(10)),
        order_type=order_type,
        limit_price=limit_price,
        trigger_price=None,
        product=Product.CNC,
        client_request_id=client_request_id,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_valid_limit_proposal_constructs() -> None:
    proposal = _make()
    assert proposal.mode is Mode.PAPER
    assert proposal.limit_price is not None


def test_valid_market_proposal_constructs() -> None:
    proposal = _make(order_type=OrderType.MARKET, limit_price=None)
    assert proposal.limit_price is None


def test_limit_order_without_price_is_rejected() -> None:
    with pytest.raises(InvalidTradeProposalError, match="LIMIT orders require"):
        _make(order_type=OrderType.LIMIT, limit_price=None)


def test_market_order_with_price_is_rejected() -> None:
    with pytest.raises(InvalidTradeProposalError, match="MARKET orders must not"):
        _make(order_type=OrderType.MARKET, limit_price=Price(Decimal("100")))


def test_timezone_naive_created_at_is_rejected() -> None:
    with pytest.raises(InvalidTradeProposalError, match="timezone-aware"):
        _make(created_at=datetime(2026, 1, 1))


def test_empty_client_request_id_is_rejected() -> None:
    with pytest.raises(InvalidTradeProposalError, match="client_request_id"):
        _make(client_request_id="   ")


def test_mode_is_required_and_cannot_silently_change() -> None:
    proposal = _make(mode=Mode.PAPER)
    assert proposal.mode is Mode.PAPER

    with pytest.raises(dataclasses.FrozenInstanceError):
        proposal.mode = Mode.LIVE  # type: ignore[misc]

    # The original instance is provably unaffected by the failed attempt.
    assert proposal.mode is Mode.PAPER


def test_expected_risk_defaults_to_empty_and_is_read_only() -> None:
    proposal = _make()
    assert dict(proposal.expected_risk) == {}
    with pytest.raises(TypeError):
        proposal.expected_risk["x"] = "y"  # type: ignore[index]
