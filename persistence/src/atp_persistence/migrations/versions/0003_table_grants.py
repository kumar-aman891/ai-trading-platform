"""Table-level least-privilege grants, replacing the coarse schema-level
defaults `ops/sql/roles_and_schemas.sql.tmpl`'s `ALTER DEFAULT PRIVILEGES`
statements applied automatically when migration 0001 created each table.

That file says explicitly (see its own header comment): "per-table security
boundaries documented in docs/schemas/*.md... are narrower than this file
grants and are expected to be applied as per-table REVOKE/GRANT statements
in the Step 6+ migration... That narrowing is deliberately NOT implemented
[t]here." This migration is that narrowing - every REVOKE below removes a
privilege the coarse baseline granted automatically but a specific
docs/schemas/*.md "Security boundary" section says the role must not have.

Nothing here ever grants UPDATE/DELETE on `audit.*` to any role, grants any
privilege on `live.*` to any application role, or grants a role more than
its docs/schemas/*.md security-boundary section describes.

Revision ID: 0003_table_grants
Revises: 0002_seed_fixture_instruments
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_table_grants"
down_revision: str | None = "0002_seed_fixture_instruments"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

# Each entry: (schema.table, role, privileges to revoke from the Step 5
# coarse baseline). The comment on each line cites the docs/schemas/*.md
# "Security boundary" section that justifies it.
_REVOKES: tuple[tuple[str, str, str], ...] = (
    # core.users: "Readable by atp_api only; no other service role has
    # SELECT." atp_api itself never deletes a user (user.md: "disabling a
    # user does not delete history").
    ("core.users", "atp_api", "DELETE"),
    ("core.users", "atp_paper_exec", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.users", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    # core.sessions: "Readable/writable by atp_api only." atp_worker's
    # narrower column-scoped access is granted separately below.
    ("core.sessions", "atp_paper_exec", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.sessions", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    # core.instruments: no Phase 1 route mutates it; only a future loader
    # (via atp_owner) or a migration seed does (instrument.md).
    ("core.instruments", "atp_api", "INSERT, UPDATE, DELETE"),
    ("core.instruments", "atp_worker", "INSERT, UPDATE, DELETE"),
    # core.risk_config: "No API route in Phase 1 mutates this table."
    ("core.risk_config", "atp_api", "INSERT, UPDATE, DELETE"),
    ("core.risk_config", "atp_worker", "INSERT, UPDATE, DELETE"),
    # core.kill_switch_state: "Write access is atp_api-only" - engage/
    # disengage/create-on-demand never deletes a switch row.
    ("core.kill_switch_state", "atp_api", "DELETE"),
    ("core.kill_switch_state", "atp_worker", "INSERT, UPDATE, DELETE"),
    # core.kill_switch_history: append-only (mirrors audit.audit_events);
    # "Write access: only via the same code path that updates
    # core.kill_switch_state" (atp_api). atp_worker has no stated need.
    ("core.kill_switch_history", "atp_api", "UPDATE, DELETE"),
    ("core.kill_switch_history", "atp_paper_exec", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.kill_switch_history", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    # core.job_queue: "Read/write: atp_worker only."
    ("core.job_queue", "atp_api", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.job_queue", "atp_paper_exec", "SELECT, INSERT, UPDATE, DELETE"),
    # paper.trade_proposals: "Writable by atp_api... and readable by
    # atp_exec_paper" - atp_paper_exec never inserts/updates a proposal.
    ("paper.trade_proposals", "atp_paper_exec", "INSERT, UPDATE"),
    # paper.risk_decisions: "Written only by atp_exec_paper... Readable by
    # atp_api." Decisions are write-once - never updated after insert.
    ("paper.risk_decisions", "atp_api", "INSERT"),
    ("paper.risk_decisions", "atp_paper_exec", "UPDATE"),
    # paper.order_intents (order_intent.md): "atp_api has no INSERT grant
    # on it at all." Single-use by UNIQUE(decision_id) - never updated.
    ("paper.order_intents", "atp_api", "INSERT"),
    ("paper.order_intents", "atp_paper_exec", "UPDATE"),
    # paper.orders: "Written only by atp_exec_paper... atp_api has
    # read-only access."
    ("paper.orders", "atp_api", "INSERT"),
    # paper.fills: "Written only by atp_exec_paper. No update path exists
    # post-insert."
    ("paper.fills", "atp_api", "INSERT"),
    ("paper.fills", "atp_paper_exec", "UPDATE"),
    # paper.positions: "Written only by atp_exec_paper... Read by atp_api."
    ("paper.positions", "atp_api", "INSERT"),
    # paper.cash_ledger: "Written only by atp_exec_paper" - a running
    # balance; each entry is a new row, never updated.
    ("paper.cash_ledger", "atp_api", "INSERT"),
    ("paper.cash_ledger", "atp_paper_exec", "UPDATE"),
)

# Re-derived, exactly, from the coarse Step 5 baseline
# (ops/sql/roles_and_schemas.sql.tmpl's `ALTER DEFAULT PRIVILEGES`) so
# downgrade() can restore precisely what upgrade() revoked - not "whatever
# seems reasonable now."
_STEP5_BASELINE: tuple[tuple[str, str, str], ...] = (
    ("core.users", "atp_api", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.users", "atp_paper_exec", "SELECT"),
    ("core.users", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.sessions", "atp_paper_exec", "SELECT"),
    ("core.sessions", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.instruments", "atp_api", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.instruments", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.risk_config", "atp_api", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.risk_config", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.kill_switch_state", "atp_api", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.kill_switch_state", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.kill_switch_history", "atp_api", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.kill_switch_history", "atp_paper_exec", "SELECT"),
    ("core.kill_switch_history", "atp_worker", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.job_queue", "atp_api", "SELECT, INSERT, UPDATE, DELETE"),
    ("core.job_queue", "atp_paper_exec", "SELECT"),
    ("paper.trade_proposals", "atp_paper_exec", "SELECT, INSERT, UPDATE"),
    ("paper.risk_decisions", "atp_api", "SELECT, INSERT"),
    ("paper.risk_decisions", "atp_paper_exec", "SELECT, INSERT, UPDATE"),
    ("paper.order_intents", "atp_api", "SELECT, INSERT"),
    ("paper.order_intents", "atp_paper_exec", "SELECT, INSERT, UPDATE"),
    ("paper.orders", "atp_api", "SELECT, INSERT"),
    ("paper.fills", "atp_api", "SELECT, INSERT"),
    ("paper.fills", "atp_paper_exec", "SELECT, INSERT, UPDATE"),
    ("paper.positions", "atp_api", "SELECT, INSERT"),
    ("paper.cash_ledger", "atp_api", "SELECT, INSERT"),
    ("paper.cash_ledger", "atp_paper_exec", "SELECT, INSERT, UPDATE"),
)

_WORKER_SESSION_COLUMNS = ("session_id_hash", "expires_at", "revoked_at")


def upgrade() -> None:
    for table, role, privileges in _REVOKES:
        op.execute(sa.text(f"REVOKE {privileges} ON {table} FROM {role}"))

    # core.sessions: "atp_worker has read-only access scoped to
    # (session_id_hash, expires_at, revoked_at)" - session.md. Column-level
    # GRANT, not table-level, since atp_worker must never read
    # `csrf_token`/`user_id`.
    op.execute(
        sa.text(
            f"GRANT SELECT ({', '.join(_WORKER_SESSION_COLUMNS)}) " "ON core.sessions TO atp_worker"
        )
    )

    # Defensive, explicit, redundant-by-design with the schema-level
    # absence of USAGE on `live` (ADR-005 §5.4) - documents the intent even
    # though `live` holds no tables in Phase 1 for this to affect.
    op.execute(
        sa.text(
            "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA live "
            "FROM atp_api, atp_paper_exec, atp_worker"
        )
    )

    # audit.audit_events: append-only (ADR-010). The coarse baseline never
    # granted UPDATE/DELETE/TRUNCATE here to begin with (Step 5's own
    # default-privilege statements only ever grant SELECT/INSERT on
    # `audit`), but this REVOKE is issued explicitly and unconditionally
    # anyway - docs/schemas/audit_event.md specifies it as its own,
    # independent enforcement layer, not merely "whatever the default
    # happened to omit."
    op.execute(
        sa.text(
            "REVOKE UPDATE, DELETE, TRUNCATE ON audit.audit_events "
            "FROM PUBLIC, atp_api, atp_paper_exec, atp_worker"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "GRANT UPDATE, DELETE, TRUNCATE ON audit.audit_events "
            "TO atp_api, atp_paper_exec, atp_worker"
        )
    )
    op.execute(
        sa.text(
            "REVOKE SELECT (session_id_hash, expires_at, revoked_at) ON core.sessions FROM atp_worker"
        )
    )

    for table, role, privileges in _STEP5_BASELINE:
        op.execute(sa.text(f"GRANT {privileges} ON {table} TO {role}"))
