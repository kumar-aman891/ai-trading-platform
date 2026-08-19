"""Constraints that make `core.job_queue`'s claim protocol safe against
concurrent claimants and malformed rows, per ADR-013 ("Operational Worker
Scope"). Zero new columns - every value these constraints check already
exists on the table `core.job_queue` (migration 0001).

Hand-written, not generated from `Base.metadata.sorted_tables` (unlike
migration 0001): that walk only runs once, at initial schema creation: this
migration alters a table that already exists in every environment that has
run migrations 0001-0004, so it must emit `ALTER TABLE`/`CREATE INDEX`
directly rather than relying on `op.create_table`.

`atp_persistence.models.core.JobQueueRow.__table_args__` is updated to
match (same three additions, same names) so a fresh reader of the ORM
model sees the same constraints this migration adds to a live database -
but that model update has no independent effect on any already-migrated
database; every application code path in this repository creates the
schema via migrations only (never `Base.metadata.create_all`), so this
migration is the only thing that actually adds these constraints anywhere.

1. `ux_job_queue_one_live_per_type` - a partial unique index making "at
   most one PENDING-or-RUNNING row per `job_type`" a database-enforced
   invariant, not a check-then-insert race. ADR-013 §6: `atp_worker`'s
   `scheduler.py` is Phase 1's only producer, and every Phase 1 job type
   is a recurring singleton - this mirrors the existing
   `UNIQUE(proposal_id)` (migration 0001, `paper.orders`) and
   `UNIQUE(client_request_id)` (`paper.trade_proposals`) precedent:
   insert, catch `IntegrityError`, never check-then-insert.
2. `terminal_state_has_completed_at` - `completed_at` is set if and only
   if `status` is terminal (`SUCCEEDED`/`FAILED`). ADR-013 §3.
3. `attempts_within_bounds` - `attempts` never exceeds `max_attempts`,
   never goes negative, and `max_attempts` is always at least 1 (a job
   with `max_attempts = 0` could never be claimed - `attempts` increments
   at claim time, ADR-013 §3-4).

Revision ID: 0005_job_queue_claim_constraints
Revises: 0004_paper_cash_ledger_seed
Create Date: 2026-08-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_job_queue_claim_constraints"
down_revision: str | None = "0004_paper_cash_ledger_seed"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

_SCHEMA = "core"
_TABLE = "job_queue"

_UNIQUE_LIVE_JOB_INDEX = "ux_job_queue_one_live_per_type"
_TERMINAL_STATE_CHECK = "ck_job_queue_terminal_state_has_completed_at"
_ATTEMPTS_BOUNDS_CHECK = "ck_job_queue_attempts_within_bounds"


def upgrade() -> None:
    op.create_index(
        _UNIQUE_LIVE_JOB_INDEX,
        _TABLE,
        ["job_type"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("status IN ('PENDING', 'RUNNING')"),
    )
    op.create_check_constraint(
        _TERMINAL_STATE_CHECK,
        _TABLE,
        "(status IN ('SUCCEEDED', 'FAILED')) = (completed_at IS NOT NULL)",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        _ATTEMPTS_BOUNDS_CHECK,
        _TABLE,
        "attempts >= 0 AND attempts <= max_attempts AND max_attempts >= 1",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    # Reverse order of upgrade().
    op.drop_constraint(_ATTEMPTS_BOUNDS_CHECK, _TABLE, schema=_SCHEMA, type_="check")
    op.drop_constraint(_TERMINAL_STATE_CHECK, _TABLE, schema=_SCHEMA, type_="check")
    op.drop_index(_UNIQUE_LIVE_JOB_INDEX, table_name=_TABLE, schema=_SCHEMA)
