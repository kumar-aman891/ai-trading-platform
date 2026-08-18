"""Order / Fill / Position - domain models only, no persistence concerns.

Order transitions are explicit and validated: `Order.with_status()` is the
only way to change an order's status, and it raises
InvalidOrderStateTransitionError rather than silently accepting an illegal
transition. Position accounting (`Position.apply_fill`) is deterministic
Decimal arithmetic - weighted-average cost, with realized P&L computed on
reductions and reversals.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from atp_domain.errors import InvalidOrderStateTransitionError
from atp_domain.money import Money, Price, Quantity, normalize_decimal
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

_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.SUBMITTED: frozenset(
        {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


def validate_transition(current: OrderStatus, new: OrderStatus) -> None:
    if new not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidOrderStateTransitionError(
            f"Cannot transition order from {current.value} to {new.value}."
        )


@dataclass(frozen=True, slots=True)
class Order:
    """`intent_id` is the `ApprovedOrderIntent` that authorized this order
    (ADR-008) - the direct link in the TradeProposal -> RiskDecision ->
    ApprovedOrderIntent -> Order chain, not merely a persistence detail.
    Required, like `proposal_id`, rather than derived by joining through
    the proposal (that join happens to be 1:1 in Phase 1's schema, but
    that is a database constraint, not a domain invariant this type should
    depend on)."""

    internal_order_id: OrderId
    mode: Mode
    proposal_id: ProposalId
    intent_id: IntentId
    idempotency_key: str
    status: OrderStatus
    submitted_at: datetime
    acknowledged_at: datetime | None
    last_update_at: datetime

    def __post_init__(self) -> None:
        if self.submitted_at.tzinfo is None:
            raise ValueError("submitted_at must be timezone-aware.")
        if self.last_update_at.tzinfo is None:
            raise ValueError("last_update_at must be timezone-aware.")
        if self.acknowledged_at is not None and self.acknowledged_at.tzinfo is None:
            raise ValueError("acknowledged_at must be timezone-aware.")

    def with_status(self, new_status: OrderStatus, *, at: datetime) -> Order:
        validate_transition(self.status, new_status)
        if at.tzinfo is None:
            raise ValueError("`at` must be timezone-aware.")
        return dataclasses.replace(self, status=new_status, last_update_at=at)


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: FillId
    mode: Mode
    internal_order_id: OrderId
    quantity: Quantity
    price: Price
    fees: Money
    taxes: Money
    simulated: bool
    filled_at: datetime

    def __post_init__(self) -> None:
        if self.filled_at.tzinfo is None:
            raise ValueError("filled_at must be timezone-aware.")
        if self.fees.value < 0:
            raise ValueError("fees must not be negative.")
        if self.taxes.value < 0:
            raise ValueError("taxes must not be negative.")


@dataclass(frozen=True, slots=True)
class Position:
    """`quantity` is signed: positive is long, negative is short. Unlike
    Order/Fill, this does not use the (always-positive) Quantity value
    type, since direction here is intrinsic to the sign rather than
    conveyed by a separate Side field."""

    position_id: PositionId
    instrument_id: InstrumentId
    mode: Mode
    quantity: Decimal
    average_price: Price | None
    realized_pnl: Money
    unrealized_pnl: Money
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware.")
        normalized_qty = normalize_decimal(self.quantity, field_name="Position.quantity")
        object.__setattr__(self, "quantity", normalized_qty)
        if self.quantity == 0 and self.average_price is not None:
            raise ValueError("A flat position must not carry an average_price.")
        if self.quantity != 0 and self.average_price is None:
            raise ValueError("A non-flat position must carry an average_price.")

    def apply_fill(self, fill: Fill, *, side: Side, at: datetime) -> Position:
        """Weighted-average-cost accounting. Handles same-direction
        accumulation, partial reduction, and reduction-through-zero
        (reversal) - each realizes P&L on exactly the portion of the fill
        that closes existing exposure."""
        if at.tzinfo is None:
            raise ValueError("`at` must be timezone-aware.")

        signed_fill_qty = fill.quantity.value if side is Side.BUY else -fill.quantity.value
        current_qty = self.quantity

        same_direction = current_qty == 0 or (current_qty > 0) == (signed_fill_qty > 0)

        if same_direction:
            new_qty = current_qty + signed_fill_qty
            if current_qty == 0:
                new_average = fill.price
            else:
                assert self.average_price is not None  # guaranteed by __post_init__
                total_cost = (abs(current_qty) * self.average_price.value) + (
                    fill.quantity.value * fill.price.value
                )
                new_average = Price(total_cost / abs(new_qty))
            return dataclasses.replace(
                self,
                quantity=new_qty,
                average_price=new_average,
                updated_at=at,
            )

        # Opposite direction: this fill closes existing exposure, and may
        # reverse through zero into a new position on the other side.
        assert self.average_price is not None
        closing_qty = min(abs(current_qty), fill.quantity.value)
        pnl_per_unit = (fill.price.value - self.average_price.value) * (
            1 if current_qty > 0 else -1
        )
        realized_delta = Money(pnl_per_unit * closing_qty)
        new_realized = self.realized_pnl + realized_delta

        remaining_fill_qty = fill.quantity.value - closing_qty
        new_qty = current_qty + signed_fill_qty

        closing_average: Price | None
        if remaining_fill_qty == 0:
            # Exactly closes (or partially reduces without reversal).
            closing_average = self.average_price if new_qty != 0 else None
        else:
            # Reverses through zero: the excess opens a new position at
            # the fill price, on the opposite side.
            closing_average = fill.price

        return dataclasses.replace(
            self,
            quantity=new_qty,
            average_price=closing_average,
            realized_pnl=new_realized,
            updated_at=at,
        )
