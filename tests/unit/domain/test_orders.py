"""Tests for atp_domain.orders - Order state machine, Fill validation,
Position weighted-average-cost accounting."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atp_domain.errors import InvalidOrderStateTransitionError
from atp_domain.money import Money, Price, Quantity
from atp_domain.orders import Fill, Order, Position, validate_transition
from atp_domain.types import (
    FillId,
    InstrumentId,
    IntentId,
    Mode,
    OrderId,
    OrderStatus,
    PositionId,
    ProposalId,
    Side,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)


def _order(status: OrderStatus = OrderStatus.SUBMITTED) -> Order:
    return Order(
        internal_order_id=OrderId("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"),
        mode=Mode.PAPER,
        proposal_id=ProposalId("bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb"),
        intent_id=IntentId("ffffffff-ffff-7fff-8fff-ffffffffffff"),
        idempotency_key="idem-1",
        status=status,
        submitted_at=NOW,
        acknowledged_at=NOW,
        last_update_at=NOW,
    )


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (OrderStatus.SUBMITTED, OrderStatus.FILLED),
        (OrderStatus.SUBMITTED, OrderStatus.REJECTED),
        (OrderStatus.SUBMITTED, OrderStatus.CANCELLED),
    ],
)
def test_allowed_transitions_succeed(current: OrderStatus, new: OrderStatus) -> None:
    validate_transition(current, new)  # does not raise


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (OrderStatus.FILLED, OrderStatus.SUBMITTED),
        (OrderStatus.FILLED, OrderStatus.CANCELLED),
        (OrderStatus.REJECTED, OrderStatus.FILLED),
        (OrderStatus.CANCELLED, OrderStatus.FILLED),
        (OrderStatus.SUBMITTED, OrderStatus.SUBMITTED),
    ],
)
def test_invalid_transitions_raise(current: OrderStatus, new: OrderStatus) -> None:
    with pytest.raises(InvalidOrderStateTransitionError):
        validate_transition(current, new)


def test_order_with_status_returns_new_instance_and_updates_timestamp() -> None:
    order = _order()
    filled = order.with_status(OrderStatus.FILLED, at=LATER)

    assert filled.status is OrderStatus.FILLED
    assert filled.last_update_at == LATER
    assert order.status is OrderStatus.SUBMITTED  # original untouched


def test_order_with_status_rejects_invalid_transition() -> None:
    order = _order(status=OrderStatus.FILLED)
    with pytest.raises(InvalidOrderStateTransitionError):
        order.with_status(OrderStatus.SUBMITTED, at=LATER)


def test_fill_rejects_negative_fees_or_taxes() -> None:
    with pytest.raises(ValueError, match="fees"):
        Fill(
            fill_id=FillId("cccccccc-cccc-7ccc-8ccc-cccccccccccc"),
            mode=Mode.PAPER,
            internal_order_id=OrderId("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"),
            quantity=Quantity(Decimal(10)),
            price=Price(Decimal(100)),
            fees=Money(Decimal(-1)),
            taxes=Money(Decimal(0)),
            simulated=True,
            filled_at=NOW,
        )


def _fill(quantity: int, price: str) -> Fill:
    return Fill(
        fill_id=FillId("cccccccc-cccc-7ccc-8ccc-cccccccccccc"),
        mode=Mode.PAPER,
        internal_order_id=OrderId("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"),
        quantity=Quantity(Decimal(quantity)),
        price=Price(Decimal(price)),
        fees=Money.zero(),
        taxes=Money.zero(),
        simulated=True,
        filled_at=NOW,
    )


def _flat_position() -> Position:
    return Position(
        position_id=PositionId("dddddddd-dddd-7ddd-8ddd-dddddddddddd"),
        instrument_id=InstrumentId("eeeeeeee-eeee-7eee-8eee-eeeeeeeeeeee"),
        mode=Mode.PAPER,
        quantity=Decimal(0),
        average_price=None,
        realized_pnl=Money.zero(),
        unrealized_pnl=Money.zero(),
        updated_at=NOW,
    )


def test_flat_position_rejects_average_price() -> None:
    with pytest.raises(ValueError, match="flat position"):
        Position(
            position_id=PositionId("dddddddd-dddd-7ddd-8ddd-dddddddddddd"),
            instrument_id=InstrumentId("eeeeeeee-eeee-7eee-8eee-eeeeeeeeeeee"),
            mode=Mode.PAPER,
            quantity=Decimal(0),
            average_price=Price(Decimal(100)),
            realized_pnl=Money.zero(),
            unrealized_pnl=Money.zero(),
            updated_at=NOW,
        )


def test_opening_a_position_sets_average_price_to_fill_price() -> None:
    position = _flat_position().apply_fill(_fill(10, "100"), side=Side.BUY, at=LATER)
    assert position.quantity == Decimal(10)
    assert position.average_price == Price(Decimal(100))
    assert position.realized_pnl == Money.zero()


def test_accumulating_same_direction_computes_weighted_average() -> None:
    position = _flat_position().apply_fill(_fill(10, "100"), side=Side.BUY, at=LATER)
    position = position.apply_fill(_fill(10, "120"), side=Side.BUY, at=LATER)

    assert position.quantity == Decimal(20)
    assert position.average_price == Price(Decimal(110))  # (10*100 + 10*120) / 20
    assert position.realized_pnl == Money.zero()


def test_partial_reduction_realizes_pnl_and_keeps_average_price() -> None:
    position = _flat_position().apply_fill(_fill(10, "100"), side=Side.BUY, at=LATER)
    position = position.apply_fill(_fill(4, "150"), side=Side.SELL, at=LATER)

    assert position.quantity == Decimal(6)
    assert position.average_price == Price(Decimal(100))  # unchanged for the open remainder
    assert position.realized_pnl == Money(Decimal(200))  # (150-100)*4


def test_exact_close_leaves_position_flat_with_no_average_price() -> None:
    position = _flat_position().apply_fill(_fill(10, "100"), side=Side.BUY, at=LATER)
    position = position.apply_fill(_fill(10, "90"), side=Side.SELL, at=LATER)

    assert position.quantity == Decimal(0)
    assert position.average_price is None
    assert position.realized_pnl == Money(Decimal(-100))  # (90-100)*10


def test_reversal_through_zero_opens_new_position_at_fill_price() -> None:
    position = _flat_position().apply_fill(_fill(10, "100"), side=Side.BUY, at=LATER)
    position = position.apply_fill(_fill(15, "90"), side=Side.SELL, at=LATER)

    # 10 units close the long (realizing (90-100)*10 = -100), remaining 5
    # units open a new short position at 90.
    assert position.quantity == Decimal(-5)
    assert position.average_price == Price(Decimal(90))
    assert position.realized_pnl == Money(Decimal(-100))


def test_short_position_pnl_sign_is_correct() -> None:
    position = _flat_position().apply_fill(_fill(10, "100"), side=Side.SELL, at=LATER)
    assert position.quantity == Decimal(-10)

    # Buying back cheaper than the short's average price is a profit.
    position = position.apply_fill(_fill(10, "80"), side=Side.BUY, at=LATER)
    assert position.quantity == Decimal(0)
    assert position.realized_pnl == Money(Decimal(200))  # (100-80)*10 profit on a short
