"""`core` schema: mode-agnostic reference/config/session data
(docs/schemas/user.md, session.md, instrument.md, risk_config.md,
kill_switch_state.md, kill_switch_history.md, job_queue.md).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from atp_persistence.models.base import (
    Base,
    utc_timestamp,
    utc_timestamp_nullable,
    uuid_column,
    uuid_pk,
)

_SCHEMA = "core"


class UserRow(Base):
    """`core.users` (docs/schemas/user.md). Holds `password_hash` only -
    never a plaintext password or session token."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('viewer','researcher','paper_trader','live_trader','administrator')",
            name="valid_role",
        ),
        {"schema": _SCHEMA},
    )

    user_id: Mapped[str] = uuid_pk("user_id")
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    must_change_password: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    created_at: Mapped[datetime] = utc_timestamp()
    updated_at: Mapped[datetime] = utc_timestamp()


# Case-insensitive uniqueness (docs/schemas/user.md: "UNIQUE (lower(username))").
# Declared after the class, not in __table_args__, because a functional index
# needs the bound Column object (`UserRow.username`), which does not exist
# until the class body has finished executing.
Index("uq_users_lower_username", func.lower(UserRow.username), unique=True)


class SessionRow(Base):
    """`core.sessions` (docs/schemas/session.md). PK is the SHA-256 of the
    opaque session ID - the raw ID is never persisted anywhere."""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_user_id", "user_id"),
        Index("ix_sessions_expires_at", "expires_at"),
        {"schema": _SCHEMA},
    )

    session_id_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey(f"{_SCHEMA}.users.user_id"), nullable=False)
    csrf_token: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = utc_timestamp()
    expires_at: Mapped[datetime] = utc_timestamp()
    revoked_at: Mapped[datetime | None] = utc_timestamp_nullable()
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)


class InstrumentRow(Base):
    """`core.instruments` (docs/schemas/instrument.md). Phase 1 seeds
    `provider = 'FIXTURE'` rows only - no real Kite instrument loader
    exists yet."""

    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint(
            "option_type IN ('CE','PE') OR option_type IS NULL", name="valid_option_type"
        ),
        Index(
            "uq_instruments_provider_token",
            "provider",
            "provider_instrument_token",
            unique=True,
        ),
        Index(
            "uq_instruments_active_identity",
            "exchange",
            "segment",
            "symbol",
            "expiry",
            "strike",
            "option_type",
            unique=True,
            postgresql_where=text("active_to IS NULL"),
        ),
        {"schema": _SCHEMA},
    )

    instrument_id: Mapped[str] = uuid_pk("instrument_id")
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_instrument_token: Mapped[str] = mapped_column(Text, nullable=False)
    exchange: Mapped[str] = mapped_column(Text, nullable=False)
    segment: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    strike: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    option_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    active_from: Mapped[datetime] = utc_timestamp()
    active_to: Mapped[datetime | None] = utc_timestamp_nullable()


class RiskConfigRow(Base):
    """`core.risk_config` (docs/schemas/risk_config.md). Immutable after
    insert (enforced by a trigger created in migration 0001, mirroring the
    audit-table append-only pattern) - activating a new version inserts a
    row and flips `active`, it never edits history.

    `created_by` is nullable: the Phase 1 bootstrap PAPER config is
    migration-seeded with `created_by = NULL`, mirroring
    `core.kill_switch_state.updated_by`'s existing NULL-for-system-actor
    convention, rather than fabricating a `core.users` row to satisfy the
    FK (docs/schemas/user.md: "No default/seeded user in any migration").
    Every application-driven version change must still supply a real
    administrator's `user_id` - that requirement is enforced by the
    (not-yet-built) route that creates one, not by this column's
    nullability, exactly like `kill_switch_state`'s
    `CHECK (updated_by IS NULL OR reason IS NOT NULL)` is enforced at the
    row level rather than by forbidding NULL outright."""

    __tablename__ = "risk_config"
    __table_args__ = (
        CheckConstraint("mode IN ('PAPER','LIVE')", name="valid_mode"),
        Index("uq_risk_config_mode_version", "mode", "version", unique=True),
        Index(
            "uq_risk_config_one_active_per_mode",
            "mode",
            unique=True,
            postgresql_where=text("active"),
        ),
        {"schema": _SCHEMA},
    )

    risk_config_id: Mapped[str] = uuid_pk("risk_config_id")
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    config_hash: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = utc_timestamp()
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.users.user_id"), nullable=True
    )


class KillSwitchStateRow(Base):
    """`core.kill_switch_state` (docs/schemas/kill_switch_state.md).
    Current state of the six ADR-007 switches; source of truth for the
    fail-closed kill-switch policy."""

    __tablename__ = "kill_switch_state"
    __table_args__ = (
        CheckConstraint("updated_by IS NULL OR reason IS NOT NULL", name="reason_required"),
        {"schema": _SCHEMA},
    )

    switch_id: Mapped[str] = mapped_column(Text, primary_key=True)
    engaged: Mapped[bool] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = utc_timestamp()
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.users.user_id"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class KillSwitchHistoryRow(Base):
    """`core.kill_switch_history` (docs/schemas/kill_switch_history.md).
    Every kill-switch transition, ever - append-only by the same
    grant+trigger pattern as `audit.audit_events`."""

    __tablename__ = "kill_switch_history"
    __table_args__ = ({"schema": _SCHEMA},)

    history_id: Mapped[str] = uuid_pk("history_id")
    switch_id: Mapped[str] = mapped_column(
        ForeignKey(f"{_SCHEMA}.kill_switch_state.switch_id"), nullable=False
    )
    previous_engaged: Mapped[bool] = mapped_column(nullable=False)
    new_engaged: Mapped[bool] = mapped_column(nullable=False)
    changed_at: Mapped[datetime] = utc_timestamp()
    changed_by: Mapped[str | None] = mapped_column(
        ForeignKey(f"{_SCHEMA}.users.user_id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    audit_event_id: Mapped[str] = uuid_column()


class JobQueueRow(Base):
    """`core.job_queue` (docs/schemas/job_queue.md). Durable job table
    backing `atp_worker` - no Celery, no message broker in Phase 1."""

    __tablename__ = "job_queue"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('SESSION_REAP','AUDIT_INTEGRITY_CHECK','RETENTION')",
            name="valid_job_type",
        ),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')", name="valid_status"
        ),
        Index(
            "ix_job_queue_pending_schedule",
            "status",
            "scheduled_for",
            postgresql_where=text("status = 'PENDING'"),
        ),
        {"schema": _SCHEMA},
    )

    job_id: Mapped[str] = uuid_pk("job_id")
    job_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    scheduled_for: Mapped[datetime] = utc_timestamp()
    locked_at: Mapped[datetime | None] = utc_timestamp_nullable()
    locked_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = utc_timestamp_nullable()
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = utc_timestamp()


__all__ = [
    "UserRow",
    "SessionRow",
    "InstrumentRow",
    "RiskConfigRow",
    "KillSwitchStateRow",
    "KillSwitchHistoryRow",
    "JobQueueRow",
]
