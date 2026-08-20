"""`paper` schema: PAPER-mode execution-path state
(docs/schemas/trade_proposal.md, risk_decision.md, order_intent.md,
order.md, fill.md, position.md, cash_ledger.md).

Every table here carries `CHECK (mode = 'PAPER')` in addition to living in
the `paper` schema - redundant with the schema boundary by design
(ADR-005 §6). No table in this module has a `live` counterpart yet; `live`
is created empty in Phase 1 (see models/live.py).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atp_persistence.models.base import (
    Base,
    utc_timestamp,
    utc_timestamp_nullable,
    uuid_column,
    uuid_pk,
)

_SCHEMA = "paper"
_MODE_CHECK = "mode = 'PAPER'"


class TradeProposalRow(Base):
    """`paper.trade_proposals` (docs/schemas/trade_proposal.md).

    `created_by` is nullable (ADR-015): a strategy-authored proposal has no
    `core.users` row to point at - no migration seeds one
    (docs/schemas/user.md's "no default/seeded user, ever" rule), and no
    Phase 1 role may read `core.users` except `atp_api`. `created_by`
    mirrors `core.risk_config.created_by`/`core.kill_switch_state.updated_by`'s
    existing NULL-for-non-human-actor convention. The `proposal_has_an_author`
    CHECK is what keeps every row attributable despite the loosened column:
    a human proposal carries `created_by`, a strategy proposal carries
    `strategy_id`, and at least one of the two is always required.
    """

    __tablename__ = "trade_proposals"
    __table_args__ = (
        CheckConstraint(_MODE_CHECK, name="mode_is_paper"),
        CheckConstraint("side IN ('BUY','SELL')", name="valid_side"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("order_type IN ('MARKET','LIMIT')", name="valid_order_type"),
        CheckConstraint(
            "(order_type = 'LIMIT') = (limit_price IS NOT NULL)",
            name="limit_price_iff_limit_order",
        ),
        CheckConstraint("product IN ('CNC','MIS')", name="valid_product"),
        CheckConstraint(
            "created_by IS NOT NULL OR strategy_id IS NOT NULL",
            name="proposal_has_an_author",
        ),
        {"schema": _SCHEMA},
    )

    proposal_id: Mapped[str] = uuid_pk("proposal_id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("core.instruments.instrument_id"), nullable=False
    )
    side: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    order_type: Mapped[str] = mapped_column(Text, nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    trigger_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    product: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_id: Mapped[str | None] = uuid_column(nullable=True)
    strategy_version: Mapped[int | None] = mapped_column(nullable=True)
    source_signal_id: Mapped[str | None] = uuid_column(nullable=True)
    client_request_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expected_risk: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("core.users.user_id"), nullable=True)
    created_at: Mapped[datetime] = utc_timestamp()


class RiskDecisionRow(Base):
    """`paper.risk_decisions` (docs/schemas/risk_decision.md). Written for
    every evaluation, approvals and rejections alike."""

    __tablename__ = "risk_decisions"
    __table_args__ = (
        CheckConstraint(_MODE_CHECK, name="mode_is_paper"),
        CheckConstraint("outcome IN ('APPROVED','REJECTED')", name="valid_outcome"),
        {"schema": _SCHEMA},
    )

    decision_id: Mapped[str] = uuid_pk("decision_id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey(f"{_SCHEMA}.trade_proposals.proposal_id"), nullable=False, unique=True
    )
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    rule_results: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    risk_config_id: Mapped[str] = mapped_column(
        ForeignKey("core.risk_config.risk_config_id"), nullable=False
    )
    limit_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = utc_timestamp()


class OrderIntentRow(Base):
    """`paper.order_intents` (docs/schemas/order_intent.md, ADR-008). The
    only artifact a broker adapter's `submit()` may ever accept. Minted
    only by `atp_domain.risk.engine`; single-use by the
    `UNIQUE (decision_id)` constraint below."""

    __tablename__ = "order_intents"
    __table_args__ = (
        CheckConstraint(_MODE_CHECK, name="mode_is_paper"),
        {"schema": _SCHEMA},
    )

    intent_id: Mapped[str] = uuid_pk("intent_id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    decision_id: Mapped[str] = mapped_column(
        ForeignKey(f"{_SCHEMA}.risk_decisions.decision_id"), nullable=False, unique=True
    )
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey(f"{_SCHEMA}.trade_proposals.proposal_id"), nullable=False
    )
    canonical_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    minted_at: Mapped[datetime] = utc_timestamp()
    expires_at: Mapped[datetime] = utc_timestamp()


class OrderRow(Base):
    """`paper.orders` (docs/schemas/order.md). `broker_order_id`/
    `broker_provider` are always null in Phase 1 - no broker adapter
    exists (ADR-008)."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(_MODE_CHECK, name="mode_is_paper"),
        CheckConstraint(
            "(broker_order_id IS NULL) = (broker_provider IS NULL)",
            name="broker_id_and_provider_paired",
        ),
        CheckConstraint(
            "status IN ('SUBMITTED','FILLED','REJECTED','CANCELLED')", name="valid_status"
        ),
        {"schema": _SCHEMA},
    )

    internal_order_id: Mapped[str] = uuid_pk("internal_order_id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    broker_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey(f"{_SCHEMA}.trade_proposals.proposal_id"), nullable=False, unique=True
    )
    intent_id: Mapped[str] = mapped_column(
        ForeignKey(f"{_SCHEMA}.order_intents.intent_id"), nullable=False, unique=True
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = utc_timestamp()
    acknowledged_at: Mapped[datetime | None] = utc_timestamp_nullable()
    last_update_at: Mapped[datetime] = utc_timestamp()


class FillRow(Base):
    """`paper.fills` (docs/schemas/fill.md). Deliberately fake in Phase 1 -
    `simulated` is a required, non-optional column so a future real fill
    is structurally distinguishable from a simulated one."""

    __tablename__ = "fills"
    __table_args__ = (
        CheckConstraint(_MODE_CHECK, name="mode_is_paper"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("fees >= 0 AND taxes >= 0", name="nonnegative_fees_and_taxes"),
        {"schema": _SCHEMA},
    )

    fill_id: Mapped[str] = uuid_pk("fill_id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    internal_order_id: Mapped[str] = mapped_column(
        ForeignKey(f"{_SCHEMA}.orders.internal_order_id"), nullable=False
    )
    broker_trade_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    fees: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    taxes: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    simulated: Mapped[bool] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    filled_at: Mapped[datetime] = utc_timestamp()


class PositionRow(Base):
    """`paper.positions` (docs/schemas/position.md). An ordinary mutable
    relational row, updated on each fill - not derived by folding over an
    event log (ADR-010)."""

    __tablename__ = "positions"
    __table_args__ = (
        CheckConstraint(_MODE_CHECK, name="mode_is_paper"),
        Index("uq_positions_mode_instrument", "mode", "instrument_id", unique=True),
        {"schema": _SCHEMA},
    )

    position_id: Mapped[str] = uuid_pk("position_id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("core.instruments.instrument_id"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    average_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, server_default="0"
    )
    updated_at: Mapped[datetime] = utc_timestamp()


class CashLedgerRow(Base):
    """`paper.cash_ledger` (docs/schemas/cash_ledger.md). A running
    balance, not a double-entry general ledger - Phase 1 has exactly one
    simulated cash account per mode."""

    __tablename__ = "cash_ledger"
    __table_args__ = (
        CheckConstraint(_MODE_CHECK, name="mode_is_paper"),
        CheckConstraint(
            "entry_type IN ('DEPOSIT','FILL_DEBIT','FILL_CREDIT')", name="valid_entry_type"
        ),
        CheckConstraint("amount > 0", name="positive_amount"),
        CheckConstraint(
            "(entry_type = 'DEPOSIT') = (related_fill_id IS NULL)",
            name="related_fill_iff_not_deposit",
        ),
        {"schema": _SCHEMA},
    )

    entry_id: Mapped[str] = uuid_pk("entry_id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    related_fill_id: Mapped[str | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.fills.fill_id"), nullable=True
    )
    balance_after: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    created_at: Mapped[datetime] = utc_timestamp()


__all__ = [
    "TradeProposalRow",
    "RiskDecisionRow",
    "OrderIntentRow",
    "OrderRow",
    "FillRow",
    "PositionRow",
    "CashLedgerRow",
]
