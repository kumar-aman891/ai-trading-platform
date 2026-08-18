"""core + audit + paper schema: every Phase 1 table, plus the append-only
enforcement triggers for audit.audit_events / core.kill_switch_history, plus
the immutability trigger for core.risk_config, plus the four seeded
kill-switch rows, plus the one seeded bootstrap PAPER risk config row.

Table DDL is generated from `Base.metadata` (`atp_persistence.models`)
rather than hand-transcribed a second time, so there is exactly one
reviewed source of truth for every column/constraint/index - the ORM model
files themselves, already reviewed against docs/schemas/*.md. This is not
`alembic revision --autogenerate` (which diffs live DB state against
metadata and can silently include unintended changes); it is a fixed,
already-known table list, created in dependency order and logged so the
generated SQL is inspectable via `alembic upgrade head --sql`.

Step 6 architecture reconciliation: docs/schemas/risk_config.md calls for
"one migration-seeded PAPER config row" with `created_by` pointing at an
administrator, while docs/schemas/user.md is equally explicit that "No
default/seeded user in any migration" exists in Phase 1. Resolved by making
`core.risk_config.created_by` nullable (see `atp_persistence.models.core.
RiskConfigRow`) and seeding this one row with `created_by = NULL` -
mirroring `core.kill_switch_state.updated_by`'s existing NULL-for-system
convention rather than fabricating a `core.users` row. No `core.users` row
is created by this or any other migration.

Revision ID: 0001_core_audit_paper_schema
Revises:
Create Date: 2026-08-18
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from atp_domain.ids import UUIDv7Generator
from atp_domain.money import Money
from atp_domain.risk.config import RiskConfig
from atp_domain.types import Mode, RiskConfigId
from atp_persistence.models import Base

revision: str = "0001_core_audit_paper_schema"
down_revision: str | None = None
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

# Phase 1 has no documented numeric default for this limit
# (docs/RISK_AND_GUARDRAILS.md describes the rule catalog, not concrete
# figures) - this is the migration's own deliberately conservative
# starting value for the PAPER bootstrap config, not a value derived from
# any spec. Revisit once a real admin-authored config exists.
_BOOTSTRAP_MAX_ORDER_NOTIONAL = Decimal("1000000.000000")

# core/audit/paper are provisioned by ops/sql/roles_and_schemas.sql.tmpl at
# container bootstrap. Re-asserted here, idempotently, so this migration
# also succeeds standalone against a database that only ran a plain
# `CREATE DATABASE` (e.g. local `alembic upgrade head` without the compose
# bootstrap having run first) - `live` is included for the same reason,
# though no table is ever created inside it (ADR-005 §5.4).
_SCHEMAS = ("core", "audit", "paper", "live")

_KILL_SWITCH_SEED_ROWS = (
    ("GLOBAL_LIVE", True),
    ("LIVE_ACCOUNT", True),
    ("PAPER", False),
    ("API_EXECUTION", False),
)


def upgrade() -> None:
    for schema in _SCHEMAS:
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    for table in Base.metadata.sorted_tables:
        table.create(bind=op.get_bind(), checkfirst=False)

    # --- Append-only enforcement: audit.audit_events (ADR-010) -----------
    op.execute(
        sa.text(
            """
            CREATE FUNCTION audit.reject_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'audit.audit_events is append-only: % is not permitted', TG_OP;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER audit_events_append_only
            BEFORE UPDATE OR DELETE ON audit.audit_events
            FOR EACH ROW EXECUTE FUNCTION audit.reject_mutation()
            """
        )
    )

    # --- Append-only enforcement: core.kill_switch_history ----------------
    op.execute(
        sa.text(
            """
            CREATE FUNCTION core.reject_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only: % is not permitted', TG_TABLE_NAME, TG_OP;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER kill_switch_history_append_only
            BEFORE UPDATE OR DELETE ON core.kill_switch_history
            FOR EACH ROW EXECUTE FUNCTION core.reject_mutation()
            """
        )
    )

    # --- Immutability: core.risk_config (docs/schemas/risk_config.md) ----
    # Every column except `active` is immutable after insert; activating a
    # new version inserts a row and flips `active`, it never edits history.
    op.execute(
        sa.text(
            """
            CREATE FUNCTION core.reject_risk_config_mutation() RETURNS trigger AS $$
            BEGIN
                IF NEW.config IS DISTINCT FROM OLD.config
                   OR NEW.config_hash IS DISTINCT FROM OLD.config_hash
                   OR NEW.mode IS DISTINCT FROM OLD.mode
                   OR NEW.version IS DISTINCT FROM OLD.version
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
                    RAISE EXCEPTION
                        'core.risk_config rows are immutable except active - insert a new version instead';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER risk_config_immutable
            BEFORE UPDATE ON core.risk_config
            FOR EACH ROW EXECUTE FUNCTION core.reject_risk_config_mutation()
            """
        )
    )

    # --- Seed: core.kill_switch_state (docs/schemas/kill_switch_state.md) -
    for switch_id, engaged in _KILL_SWITCH_SEED_ROWS:
        op.execute(
            sa.text(
                """
                INSERT INTO core.kill_switch_state (switch_id, engaged, updated_at)
                VALUES (:switch_id, :engaged, now())
                """
            ).bindparams(switch_id=switch_id, engaged=engaged)
        )

    # --- Seed: core.risk_config bootstrap PAPER row (docs/schemas/risk_config.md) -
    # `created_by = NULL` (see module docstring); config_hash is computed
    # by the same domain property RiskDecision.limit_snapshot_hash binds
    # against, so this row is never out of sync with how the risk engine
    # itself would hash an identical config.
    #
    # Inserted via a plain parameterized `op.execute`, not `op.bulk_insert`,
    # because SQLAlchemy has no offline-mode (`alembic ... --sql`) literal
    # renderer for JSONB values - `op.bulk_insert` fails when rendering
    # static SQL, even though it works fine online. Binding `config` as a
    # JSON-encoded string and casting it server-side (`:config::jsonb`)
    # sidesteps that limitation in both modes.
    _bootstrap_config = RiskConfig(
        risk_config_id=RiskConfigId(UUIDv7Generator().new_id()),
        mode=Mode.PAPER,
        version=1,
        max_order_notional=Money(_BOOTSTRAP_MAX_ORDER_NOTIONAL),
        created_at=datetime.now(UTC),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO core.risk_config
                (risk_config_id, mode, version, config, config_hash, active, created_at, created_by)
            VALUES
                (:risk_config_id, :mode, :version, CAST(:config AS jsonb), :config_hash, :active,
                 :created_at, NULL)
            """
        ).bindparams(
            risk_config_id=_bootstrap_config.risk_config_id,
            mode=_bootstrap_config.mode.value,
            version=_bootstrap_config.version,
            config=json.dumps(
                {"max_order_notional": str(_bootstrap_config.max_order_notional.value)}
            ),
            config_hash=_bootstrap_config.config_hash,
            active=True,
            created_at=_bootstrap_config.created_at,
        )
    )


def downgrade() -> None:
    # Bootstrap risk_config row: identified by its deterministic seed
    # criteria (mode/version/created_by), not by its randomly generated
    # risk_config_id, which is not preserved across separate upgrade()/
    # downgrade() invocations.
    op.execute(
        sa.text(
            "DELETE FROM core.risk_config WHERE mode = 'PAPER' AND version = 1 "
            "AND created_by IS NULL"
        )
    )

    op.execute(
        sa.text("DELETE FROM core.kill_switch_state WHERE switch_id = ANY(:ids)").bindparams(
            sa.bindparam(
                "ids", value=[row[0] for row in _KILL_SWITCH_SEED_ROWS], type_=sa.ARRAY(sa.Text)
            )
        )
    )

    op.execute(sa.text("DROP TRIGGER IF EXISTS risk_config_immutable ON core.risk_config"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS core.reject_risk_config_mutation()"))
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS kill_switch_history_append_only ON core.kill_switch_history"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS core.reject_mutation()"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_events_append_only ON audit.audit_events"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS audit.reject_mutation()"))

    for table in reversed(Base.metadata.sorted_tables):
        table.drop(bind=op.get_bind(), checkfirst=False)

    # Schemas themselves are left in place - they are owned by
    # ops/sql/roles_and_schemas.sql.tmpl's bootstrap step, not this
    # migration; dropping them here could destroy the role grants that
    # step applied.
