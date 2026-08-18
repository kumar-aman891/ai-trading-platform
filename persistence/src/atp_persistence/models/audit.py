"""`audit` schema: the immutable event/decision ledger
(docs/schemas/audit_event.md, ADR-010).

Append-only is enforced at two independent layers, neither implemented in
this module: a `REVOKE UPDATE, DELETE, TRUNCATE` (migration 0003) and a
`BEFORE UPDATE OR DELETE` rejecting trigger (migration 0001). This module
only declares the table shape.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atp_persistence.models.base import Base, utc_timestamp, uuid_column, uuid_pk

_SCHEMA = "audit"


class AuditEventRow(Base):
    """`audit.audit_events` (docs/schemas/audit_event.md,
    docs/OBSERVABILITY.md). `payload` is redacted at write time by
    `atp_platform.redaction` before ever reaching this column - this
    module does not redact, it only stores."""

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('USER','AGENT','SYSTEM','BROKER')", name="valid_actor_type"
        ),
        CheckConstraint("mode IS NULL OR mode IN ('PAPER','LIVE')", name="valid_mode"),
        CheckConstraint(
            "(broker_order_id IS NULL) = (broker_provider IS NULL)",
            name="broker_id_and_provider_paired",
        ),
        Index("ix_audit_events_correlation_id", "correlation_id"),
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_mode_action_occurred_at", "mode", "action", "occurred_at"),
        Index(
            "ix_audit_events_instrument_id",
            "instrument_id",
            postgresql_where=text("instrument_id IS NOT NULL"),
        ),
        {"schema": _SCHEMA},
    )

    event_id: Mapped[str] = uuid_pk("event_id")
    correlation_id: Mapped[str] = uuid_column()
    occurred_at: Mapped[datetime] = utc_timestamp()
    recorded_at: Mapped[datetime] = utc_timestamp()
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str | None] = mapped_column(Text, nullable=True)
    strategy_id: Mapped[str | None] = uuid_column(nullable=True)
    strategy_version: Mapped[int | None] = mapped_column(nullable=True)
    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("core.instruments.instrument_id"), nullable=True
    )
    source_refs: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    input_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_rule_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    broker_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)


__all__ = ["AuditEventRow"]
