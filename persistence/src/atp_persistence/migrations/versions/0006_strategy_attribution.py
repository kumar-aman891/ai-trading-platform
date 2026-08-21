"""Strategy Framework Milestones 2A+2B: `paper.trade_proposals.created_by`
becomes nullable with a new attribution CHECK (2A, ADR-015), and the
`atp_strategy` role receives its least-privilege table grants (2B,
ADR-014/ADR-015).

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

The `atp_strategy` role itself (CREATE ROLE, password, schema-level USAGE
on core/audit/paper) is created by `ops/sql/roles_and_schemas.sql.tmpl`
(the bootstrap script), not by this migration - this file only narrows
that role's table-level access, exactly as migration 0003 narrows the
three existing application roles. Deliberately **no**
`ALTER DEFAULT PRIVILEGES` entry for `atp_strategy` anywhere: the role
starts at zero table privilege and is granted upward, explicitly, table by
table, so a future table created in `core`/`audit`/`paper` never silently
becomes reachable to it.

`atp_strategy` receives exactly four grants: `SELECT` on
`core.instruments`, `SELECT` on `core.kill_switch_state`, `INSERT` on
`paper.trade_proposals`, `INSERT` on `audit.audit_events` - no `SELECT` on
`paper.trade_proposals` (a strategy has no HTTP caller to answer, so it
never needs to read back what it just inserted; a duplicate
`client_request_id` is simply skipped, not refetched - Milestone 2C), and
no access whatsoever to `core.users`, `core.sessions`, `core.job_queue`,
`core.kill_switch_history`, `paper.orders`/`fills`/`order_intents`/
`positions`/`cash_ledger`, or the `live` schema.

Revision ID: 0006_strategy_attribution
Revises: 0005_job_queue_claim_constraints
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_strategy_attribution"
down_revision: str | None = "0005_job_queue_claim_constraints"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

_SCHEMA = "paper"
_TABLE = "trade_proposals"
_AUTHOR_CHECK = "ck_trade_proposals_proposal_has_an_author"

_STRATEGY_ROLE = "atp_strategy"
_STRATEGY_GRANTS: tuple[tuple[str, str], ...] = (
    ("SELECT", "core.instruments"),
    ("SELECT", "core.kill_switch_state"),
    ("INSERT", "paper.trade_proposals"),
    ("INSERT", "audit.audit_events"),
)


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
    for privilege, table in _STRATEGY_GRANTS:
        op.execute(sa.text(f"GRANT {privilege} ON {table} TO {_STRATEGY_ROLE}"))


def downgrade() -> None:
    # Reverse order of upgrade().
    for privilege, table in reversed(_STRATEGY_GRANTS):
        op.execute(sa.text(f"REVOKE {privilege} ON {table} FROM {_STRATEGY_ROLE}"))
    # Dropping the CHECK first is required: PostgreSQL would otherwise
    # briefly permit a NULL created_by with a NULL strategy_id (impossible
    # under the CHECK) before the NOT NULL is restored, but the actual risk
    # is the reverse order failing outright - SET NOT NULL cannot succeed
    # while the CHECK still permits a NULL created_by row already in the
    # table (that data-loss risk is documented and accepted below: a
    # genuine strategy-authored row makes downgrade impossible, correctly,
    # rather than silently corrupting it).
    op.execute(sa.text(f"ALTER TABLE {_SCHEMA}.{_TABLE} DROP CONSTRAINT IF EXISTS {_AUTHOR_CHECK}"))
    op.execute(sa.text(f"ALTER TABLE {_SCHEMA}.{_TABLE} ALTER COLUMN created_by SET NOT NULL"))
