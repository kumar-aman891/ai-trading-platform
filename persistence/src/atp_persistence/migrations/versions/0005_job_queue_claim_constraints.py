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
model sees the same constraints this migration adds to a live database.
That created a real, confirmed interaction the first time this migration
ran against actual PostgreSQL: migration 0001's `upgrade()` does not
create tables from a frozen snapshot - it iterates
`Base.metadata.sorted_tables` and calls `table.create(bind=..., checkfirst
=False)` at the time *this* migration chain runs, against whatever
`atp_persistence.models` currently defines. `Table.create()` emits every
`Index` and inline `CheckConstraint` attached to a table's
`__table_args__`, not just its columns - so updating `JobQueueRow`
alongside this migration made 0001 create `ux_job_queue_one_live_per_type`
and both new CHECK constraints itself, moments before this migration's own
`upgrade()` tried to create them again, raising `DuplicateTable`.

Every statement below is therefore written to be idempotent against that
reality rather than assuming a blank slate: correct whether 0001 already
created these objects (any environment migrated after the model update)
or not (any environment migrated - and therefore already sitting at 0004
- before the model update existed). `downgrade()` mirrors this with
`IF EXISTS`, so it is equally safe to run more than once.

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
    # `CREATE INDEX ... IF NOT EXISTS` - genuine PostgreSQL syntax (unlike
    # `ADD CONSTRAINT IF NOT EXISTS`, which does not exist for CHECK
    # constraints), so this one is a single statement.
    op.execute(
        sa.text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_UNIQUE_LIVE_JOB_INDEX} "
            f"ON {_SCHEMA}.{_TABLE} (job_type) "
            "WHERE status IN ('PENDING', 'RUNNING')"
        )
    )
    # PostgreSQL has no `ADD CONSTRAINT IF NOT EXISTS` for CHECK
    # constraints - guard with an explicit `pg_constraint` lookup instead.
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{_TERMINAL_STATE_CHECK}'
                      AND conrelid = '{_SCHEMA}.{_TABLE}'::regclass
                ) THEN
                    ALTER TABLE {_SCHEMA}.{_TABLE}
                        ADD CONSTRAINT {_TERMINAL_STATE_CHECK}
                        CHECK ((status IN ('SUCCEEDED', 'FAILED')) = (completed_at IS NOT NULL));
                END IF;
            END $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{_ATTEMPTS_BOUNDS_CHECK}'
                      AND conrelid = '{_SCHEMA}.{_TABLE}'::regclass
                ) THEN
                    ALTER TABLE {_SCHEMA}.{_TABLE}
                        ADD CONSTRAINT {_ATTEMPTS_BOUNDS_CHECK}
                        CHECK (attempts >= 0 AND attempts <= max_attempts AND max_attempts >= 1);
                END IF;
            END $$
            """
        )
    )


def downgrade() -> None:
    # Reverse order of upgrade(). `DROP CONSTRAINT IF EXISTS` and
    # `DROP INDEX IF EXISTS` are both genuine PostgreSQL syntax, so
    # downgrade() needs no guard beyond that - safe to run against a
    # database in either of upgrade()'s two starting states.
    op.execute(
        sa.text(
            f"ALTER TABLE {_SCHEMA}.{_TABLE} DROP CONSTRAINT IF EXISTS {_ATTEMPTS_BOUNDS_CHECK}"
        )
    )
    op.execute(
        sa.text(f"ALTER TABLE {_SCHEMA}.{_TABLE} DROP CONSTRAINT IF EXISTS {_TERMINAL_STATE_CHECK}")
    )
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_SCHEMA}.{_UNIQUE_LIVE_JOB_INDEX}"))
