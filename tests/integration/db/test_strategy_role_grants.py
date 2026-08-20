"""Strategy Framework Milestone 2B (ADR-014, ADR-015): `atp_strategy`'s
database identity, proven against a real PostgreSQL instance under the
role's own connection - not merely as reported by `atp_owner`.

Mirrors `tests/integration/db/test_table_grants.py`'s pattern, kept as a
separate file rather than folding into that module's
`EXPECTED_TABLE_PRIVILEGES` (which the docstring there states is scoped to
`atp_api`/`atp_paper_exec`/`atp_worker`) - `atp_strategy`'s grants were
approved and reviewed as a standalone unit (migration 0006's grant half)
and read most clearly as one.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

_STRATEGY_GRANTED_TABLES = {
    "core.instruments": {"SELECT"},
    "core.kill_switch_state": {"SELECT"},
    "paper.trade_proposals": {"INSERT"},
    "audit.audit_events": {"INSERT"},
}

#: Every table `atp_strategy` must hold zero privileges on - execution-
#: outcome state (ADR-014 §G), credential/session/job-queue state, and
#: anything in the `live` schema.
_STRATEGY_FORBIDDEN_TABLES = (
    "core.users",
    "core.sessions",
    "core.job_queue",
    "core.kill_switch_history",
    "paper.risk_decisions",
    "paper.order_intents",
    "paper.orders",
    "paper.fills",
    "paper.positions",
    "paper.cash_ledger",
)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _actual_privileges(conn: psycopg.Connection, table: str, *, role: str) -> set[str]:
    schema, name = table.split(".")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT privilege_type FROM information_schema.role_table_grants "
            "WHERE table_schema = %s AND table_name = %s AND grantee = %s",
            (schema, name, role),
        )
        return {row[0] for row in cur.fetchall()}


@pytest.fixture
def seeded_instrument_id(migrated_database: str, owner_connection: psycopg.Connection) -> str:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT instrument_id FROM core.instruments WHERE provider = 'FIXTURE' LIMIT 1")
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None, "expected at least one seeded FIXTURE instrument"
    return str(row[0])


@pytest.fixture
def seeded_strategy_authored_trade_proposal_cleanup(
    owner_connection: psycopg.Connection,
):
    """Cleans up any `paper.trade_proposals` row this file inserts as
    `atp_strategy` (created_by IS NULL, so `delete_user_cascade` - keyed on
    created_by - cannot reach it)."""
    proposal_ids: list[str] = []
    yield proposal_ids
    if not proposal_ids:
        return
    with owner_connection.cursor() as cur:
        cur.execute(
            "DELETE FROM paper.trade_proposals WHERE proposal_id = ANY(%s)",
            (proposal_ids,),
        )
    owner_connection.commit()


def test_strategy_role_table_grants_match_the_approved_matrix(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    for table, expected in _STRATEGY_GRANTED_TABLES.items():
        actual = _actual_privileges(owner_connection, table, role="atp_strategy")
        assert (
            actual == expected
        ), f"{table} privileges for atp_strategy: expected {expected}, got {actual}"


def test_strategy_role_has_zero_privileges_on_forbidden_tables(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    for table in _STRATEGY_FORBIDDEN_TABLES:
        actual = _actual_privileges(owner_connection, table, role="atp_strategy")
        assert actual == set(), f"atp_strategy must have zero privileges on {table}, got {actual}"


def test_strategy_role_has_no_privileges_on_the_live_schema(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege('atp_strategy', 'live', 'USAGE')")
        (has_usage,) = cur.fetchone()  # type: ignore[misc]
    owner_connection.rollback()
    assert has_usage is False


def test_strategy_role_can_select_core_instruments(
    migrated_database: str, strategy_connection: psycopg.Connection
) -> None:
    with strategy_connection.cursor() as cur:
        cur.execute("SELECT instrument_id FROM core.instruments LIMIT 1")
    strategy_connection.rollback()


def test_strategy_role_can_select_core_kill_switch_state(
    migrated_database: str, strategy_connection: psycopg.Connection
) -> None:
    with strategy_connection.cursor() as cur:
        cur.execute("SELECT switch_id, engaged FROM core.kill_switch_state LIMIT 1")
    strategy_connection.rollback()


def test_strategy_role_can_insert_trade_proposal_with_null_created_by(
    migrated_database: str,
    owner_connection: psycopg.Connection,
    strategy_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_strategy_authored_trade_proposal_cleanup: list[str],
) -> None:
    """The concrete Milestone 2A+2B interaction: a strategy-authored row -
    created_by NULL, strategy_id set - insertable by atp_strategy's own
    grant, satisfying the proposal_has_an_author CHECK."""
    proposal_id = _new_uuid()
    seeded_strategy_authored_trade_proposal_cleanup.append(proposal_id)
    with strategy_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper.trade_proposals
                (proposal_id, mode, instrument_id, side, quantity, order_type,
                 product, client_request_id, expected_risk, created_by, strategy_id,
                 strategy_version, created_at)
            VALUES (%s, 'PAPER', %s, 'BUY', 1, 'MARKET', 'CNC', %s, '{}', NULL, %s, 1, now())
            """,
            (proposal_id, seeded_instrument_id, _new_uuid(), _new_uuid()),
        )
    strategy_connection.commit()

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT created_by, strategy_id FROM paper.trade_proposals WHERE proposal_id = %s",
            (proposal_id,),
        )
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None
    created_by, strategy_id = row
    assert created_by is None
    assert strategy_id is not None


def test_strategy_role_cannot_select_trade_proposals(
    migrated_database: str, strategy_connection: psycopg.Connection
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), strategy_connection.cursor() as cur:
        cur.execute("SELECT proposal_id FROM paper.trade_proposals LIMIT 1")
    strategy_connection.rollback()


def test_strategy_role_can_insert_audit_event(
    migrated_database: str,
    owner_connection: psycopg.Connection,
    strategy_connection: psycopg.Connection,
) -> None:
    event_id = _new_uuid()
    correlation_id = _new_uuid()
    with strategy_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit.audit_events
                (event_id, correlation_id, occurred_at, recorded_at, actor_type,
                 actor_id, action, mode, decision)
            VALUES (%s, %s, now(), now(), 'AGENT', 'strategy/momentum-v1',
                    'PROPOSAL_CREATED', 'PAPER', 'APPROVED')
            """,
            (event_id, correlation_id),
        )
    strategy_connection.commit()

    with owner_connection.cursor() as cur:
        cur.execute("SELECT actor_type FROM audit.audit_events WHERE event_id = %s", (event_id,))
        row = cur.fetchone()
    owner_connection.rollback()
    assert row == ("AGENT",)


def test_strategy_role_cannot_update_or_delete_audit_events(
    migrated_database: str, strategy_connection: psycopg.Connection
) -> None:
    """The append-only trigger (ADR-010) plus the absence of any
    UPDATE/DELETE grant both apply to atp_strategy - proven here as the
    grant-level half; the trigger itself is proven generically by
    test_audit_immutability.py against atp_owner."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), strategy_connection.cursor() as cur:
        cur.execute("DELETE FROM audit.audit_events WHERE event_id = %s", (_new_uuid(),))
    strategy_connection.rollback()


def test_strategy_role_cannot_access_core_users(
    migrated_database: str, strategy_connection: psycopg.Connection
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), strategy_connection.cursor() as cur:
        cur.execute("SELECT user_id FROM core.users LIMIT 1")
    strategy_connection.rollback()


def test_strategy_role_cannot_access_core_sessions(
    migrated_database: str, strategy_connection: psycopg.Connection
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), strategy_connection.cursor() as cur:
        cur.execute("SELECT session_id_hash FROM core.sessions LIMIT 1")
    strategy_connection.rollback()


def test_strategy_role_cannot_access_core_job_queue(
    migrated_database: str, strategy_connection: psycopg.Connection
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), strategy_connection.cursor() as cur:
        cur.execute("SELECT job_id FROM core.job_queue LIMIT 1")
    strategy_connection.rollback()


@pytest.mark.parametrize(
    "table",
    [
        "paper.orders",
        "paper.fills",
        "paper.order_intents",
        "paper.positions",
        "paper.cash_ledger",
    ],
)
def test_strategy_role_cannot_access_execution_outcome_tables(
    migrated_database: str, strategy_connection: psycopg.Connection, table: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege), strategy_connection.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} LIMIT 1")
    strategy_connection.rollback()
