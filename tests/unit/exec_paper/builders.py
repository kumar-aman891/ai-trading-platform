"""Shared test-data builders for `tests/unit/exec_paper/`. Not a test file
itself - no `test_*` function lives here, so pytest does not collect it."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from atp_domain.money import Money, Price, Quantity
from atp_domain.proposals import TradeProposal
from atp_domain.risk.config import RiskConfig
from atp_domain.types import (
    InstrumentId,
    Mode,
    OrderType,
    Product,
    ProposalId,
    RiskConfigId,
    Side,
)
from atp_persistence.repositories import InstrumentSnapshot, KillSwitchStateSnapshot

NOW = datetime(2026, 1, 1, tzinfo=UTC)
INSTRUMENT_ID = InstrumentId("33333333-3333-7333-8333-333333333333")


def make_config(mode: Mode = Mode.PAPER, max_notional: str = "1000000") -> RiskConfig:
    return RiskConfig(
        risk_config_id=RiskConfigId("11111111-1111-7111-8111-111111111111"),
        mode=mode,
        version=1,
        max_order_notional=Money(Decimal(max_notional)),
        created_at=NOW,
    )


def make_instrument() -> InstrumentSnapshot:
    return InstrumentSnapshot(
        instrument_id=str(INSTRUMENT_ID),
        symbol="FIXTURE",
        lot_size=1,
        tick_size=Decimal("0.05"),
    )


def make_paper_kill_switch_snapshot(*, engaged: bool) -> KillSwitchStateSnapshot:
    return KillSwitchStateSnapshot(
        switch_id="PAPER",
        engaged=engaged,
        updated_at=NOW,
        updated_by=None,
        reason=None if not engaged else "test",
    )


def make_proposal(
    *,
    proposal_id: str = "22222222-2222-7222-8222-222222222222",
    mode: Mode = Mode.PAPER,
    order_type: OrderType = OrderType.LIMIT,
    limit_price: str | None = "100",
    quantity: str = "10",
    side: Side = Side.BUY,
    client_request_id: str = "req-1",
) -> TradeProposal:
    return TradeProposal(
        proposal_id=ProposalId(proposal_id),
        mode=mode,
        instrument_id=INSTRUMENT_ID,
        side=side,
        quantity=Quantity(Decimal(quantity)),
        order_type=order_type,
        limit_price=Price(Decimal(limit_price)) if limit_price is not None else None,
        trigger_price=None,
        product=Product.CNC,
        client_request_id=client_request_id,
        created_at=NOW,
    )
