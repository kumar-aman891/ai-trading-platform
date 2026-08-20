"""Strategy Framework Milestone 2A: `paper.trade_proposals.created_by`
becomes nullable, and a new CHECK constraint keeps every row attributable
despite the loosened column (ADR-015).

`created_by` was `NOT NULL` because every proposal to date was
human-submitted through `atp_api`. A strategy-authored proposal
(Milestone 2C onward) has no `core.users` row to point at - no migration
seeds one (docs/schemas/user.md's "no default/seeded user, ever" rule,
already relied on by `core.risk_config.created_by` and
`core.kill_switch_state.updated_by`'s existing NULL-for-non-human-actor
convention, per migration 0001's own Step 6 reconciliation note). Rather
than fabricate a `core.users` row for a principal that must never
authenticate - which would also permanently break `bootstrap_admin`
(`atp_api.bootstrap`), which refuses whenever `core.users` has any row -
`created_by` follows the same nullable convention, and `strategy_id`
(already a column on this table, previously unconstrained) becomes the
strategy-proposal attribution field.

The new CHECK makes the table net *stricter*, not weaker: before this
migration nothing prevented a row with both `created_by` and `strategy_id`
NULL; after it, every row must carry at least one.

This migration adds **no** `atp_strategy` role, grant, or any other schema
change - that is a later, separate milestone (ADR-016).

Revision ID: 0006_strategy_proposal_attribution
Revises: 0005_job_queue_claim_constraints
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_strategy_proposal_attribution"
down_revision: str | None = "0005_job_queue_claim_constraints"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

_SCHEMA = "paper"
_TABLE = "trade_proposals"
_AUTHOR_CHECK = "ck_trade_proposals_proposal_has_an_author"


def upgrade() -> None:
    op.execute(sa.text(f"ALTER TABLE {_SCHEMA}.{_TABLE} ALTER COLUMN created_by DROP NOT NULL"))
    # PostgreSQL has no `ADD CONSTRAINT IF NOT EXISTS` for CHECK
    # constraints - guard with an explicit `pg_constraint` lookup, matching
    # migration 0005's idempotent-DDL pattern.
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{_AUTHOR_CHECK}'
                      AND conrelid = '{_SCHEMA}.{_TABLE}'::regclass
                ) THEN
                    ALTER TABLE {_SCHEMA}.{_TABLE}
                        ADD CONSTRAINT {_AUTHOR_CHECK}
                        CHECK (created_by IS NOT NULL OR strategy_id IS NOT NULL);
                END IF;
            END $$
            """
        )
    )


def downgrade() -> None:
    # Reverse order of upgrade(). Dropping the CHECK first is required:
    # PostgreSQL would otherwise briefly permit a NULL created_by with a
    # NULL strategy_id (impossible under the CHECK) before the NOT NULL is
    # restored, but the actual risk is the reverse order failing outright -
    # SET NOT NULL cannot succeed while the CHECK still permits a NULL
    # created_by row already in the table (that data-loss risk is
    # documented and accepted below: a genuine strategy-authored row makes
    # downgrade impossible, correctly, rather than silently corrupting it).
    op.execute(sa.text(f"ALTER TABLE {_SCHEMA}.{_TABLE} DROP CONSTRAINT IF EXISTS {_AUTHOR_CHECK}"))
    op.execute(sa.text(f"ALTER TABLE {_SCHEMA}.{_TABLE} ALTER COLUMN created_by SET NOT NULL"))
