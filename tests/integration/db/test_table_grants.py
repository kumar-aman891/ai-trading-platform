"""Phase 1 Step 6: table-level grants match the approved matrix.

Extends the Step 5 schema-level suite (test_grant_matrix.py, which checks
`pg_default_acl` - the *template* future tables inherit) with the actual,
per-table `information_schema.role_table_grants` state after migration
0003 narrows it. `EXPECTED_TABLE_PRIVILEGES` is re-derived independently as
a literal Python constant (not by reading `0003_table_grants.py`), mirroring
the existing suite's own stated pattern, so a migration that silently
drifts from its own documented intent is caught.
"""

from __future__ import annotations

import os
import subprocess
import sys

import psycopg
import pytest

from tests.integration.db.conftest import _PERSISTENCE_DIR, _as_sync_psycopg_url

# table -> role -> expected privileges. Every table `atp_paper_exec` or
# `atp_worker` has no entry for is asserted to have zero privileges (it
# either has no USAGE on the schema at all, per Step 5, or migration 0003
# revoked everything it had).
EXPECTED_TABLE_PRIVILEGES: dict[str, dict[str, set[str]]] = {
    "core.users": {
        "atp_api": {"SELECT", "INSERT", "UPDATE"},
    },
    "core.sessions": {
        "atp_api": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    },
    "core.instruments": {
        "atp_api": {"SELECT"},
        "atp_paper_exec": {"SELECT"},
        "atp_worker": {"SELECT"},
    },
    "core.risk_config": {
        "atp_api": {"SELECT"},
        "atp_paper_exec": {"SELECT"},
        "atp_worker": {"SELECT"},
    },
    "core.kill_switch_state": {
        "atp_api": {"SELECT", "INSERT", "UPDATE"},
        "atp_paper_exec": {"SELECT"},
        "atp_worker": {"SELECT"},
    },
    "core.kill_switch_history": {
        "atp_api": {"SELECT", "INSERT"},
    },
    "core.job_queue": {
        "atp_worker": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    },
    "audit.audit_events": {
        "atp_api": {"SELECT", "INSERT"},
        "atp_paper_exec": {"INSERT"},
        "atp_worker": {"SELECT", "INSERT"},
    },
    "paper.trade_proposals": {
        "atp_api": {"SELECT", "INSERT"},
        "atp_paper_exec": {"SELECT"},
    },
    "paper.risk_decisions": {
        "atp_api": {"SELECT"},
        "atp_paper_exec": {"SELECT", "INSERT"},
    },
    "paper.order_intents": {
        "atp_api": {"SELECT"},
        "atp_paper_exec": {"SELECT", "INSERT"},
    },
    "paper.orders": {
        "atp_api": {"SELECT"},
        "atp_paper_exec": {"SELECT", "INSERT", "UPDATE"},
    },
    "paper.fills": {
        "atp_api": {"SELECT"},
        "atp_paper_exec": {"SELECT", "INSERT"},
    },
    "paper.positions": {
        "atp_api": {"SELECT"},
        "atp_paper_exec": {"SELECT", "INSERT", "UPDATE"},
    },
    "paper.cash_ledger": {
        "atp_api": {"SELECT"},
        "atp_paper_exec": {"SELECT", "INSERT"},
    },
}

_ROLES = ("atp_api", "atp_paper_exec", "atp_worker")
_MUTATING_PRIVILEGES = {"INSERT", "UPDATE", "DELETE"}


def _actual_privileges(conn: psycopg.Connection, table: str) -> dict[str, set[str]]:
    schema, name = table.split(".")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT grantee, privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = %s AND table_name = %s AND grantee = ANY(%s)
            """,
            (schema, name, list(_ROLES)),
        )
        result: dict[str, set[str]] = {}
        for grantee, privilege in cur.fetchall():
            result.setdefault(grantee, set()).add(privilege)
    return result


def test_table_grants_match_the_approved_matrix(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    for table, expected_roles in EXPECTED_TABLE_PRIVILEGES.items():
        actual = _actual_privileges(owner_connection, table)
        for role in _ROLES:
            expected = expected_roles.get(role, set())
            got = actual.get(role, set())
            assert got == expected, f"{table} privileges for {role}: expected {expected}, got {got}"


def test_audit_events_never_grants_update_or_delete_to_any_role(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    actual = _actual_privileges(owner_connection, "audit.audit_events")
    for role, privileges in actual.items():
        assert "UPDATE" not in privileges, f"audit.audit_events must never grant UPDATE to {role}"
        assert "DELETE" not in privileges, f"audit.audit_events must never grant DELETE to {role}"


def test_kill_switch_history_never_grants_update_or_delete_to_any_role(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    actual = _actual_privileges(owner_connection, "core.kill_switch_history")
    for _role, privileges in actual.items():
        assert "UPDATE" not in privileges
        assert "DELETE" not in privileges


def test_order_intents_grants_no_insert_to_api(
    migrated_database: str, api_connection: psycopg.Connection
) -> None:
    """ADR-008 / order_intent.md: "atp_api has no INSERT grant on it at
    all." Checked from atp_api's own connection, not just as reported by
    the owner role."""
    with api_connection.cursor() as cur:
        cur.execute("SELECT has_table_privilege(current_user, 'paper.order_intents', 'INSERT')")
        (can_insert,) = cur.fetchone()  # type: ignore[misc]
    assert can_insert is False


def test_worker_has_no_privileges_on_paper_schema_tables(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    """Mirrors test_schema_isolation.py's schema-level assertion at the
    table level: atp_worker has no USAGE on `paper` at all (Step 5), so it
    cannot hold a grant on any table inside it either, migration 0003
    notwithstanding."""
    for table in EXPECTED_TABLE_PRIVILEGES:
        if not table.startswith("paper."):
            continue
        actual = _actual_privileges(owner_connection, table)
        assert "atp_worker" not in actual, f"atp_worker must have zero privileges on {table}"


def test_worker_session_access_is_column_scoped(
    migrated_database: str, worker_connection: psycopg.Connection
) -> None:
    """session.md: "atp_worker has read-only access scoped to
    (session_id_hash, expires_at, revoked_at)" - proven both by what it can
    read and what it cannot."""
    with worker_connection.cursor() as cur:
        cur.execute("SELECT session_id_hash, expires_at, revoked_at FROM core.sessions LIMIT 0")

    with pytest.raises(psycopg.errors.InsufficientPrivilege), worker_connection.cursor() as cur:
        cur.execute("SELECT csrf_token FROM core.sessions LIMIT 0")
    worker_connection.rollback()

    with pytest.raises(psycopg.errors.InsufficientPrivilege), worker_connection.cursor() as cur:
        cur.execute(
            "UPDATE core.sessions SET revoked_at = now() WHERE session_id_hash = 'nonexistent'"
        )
    worker_connection.rollback()


def _run_alembic(owner_dsn: str, *args: str) -> None:
    env = os.environ.copy()
    env["ATP_MIGRATION_DATABASE_URL"] = _as_sync_psycopg_url(owner_dsn)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PERSISTENCE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic {' '.join(args)} failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def test_downgrade_of_0003_does_not_regrant_audit_mutation_privileges(
    migrated_database: str, owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    """Regression test for a defect found during the Phase 1 Step 12 Phase A
    reconciliation: `0003_table_grants.py`'s `downgrade()` used to re-grant
    UPDATE/DELETE/TRUNCATE on `audit.audit_events` to every application
    role - but the Step 5 coarse baseline
    (ops/sql/roles_and_schemas.sql.tmpl) never granted those in the first
    place, only SELECT/INSERT. A downgrade would therefore have left the
    database strictly MORE permissive than it had ever been at any point in
    this migration chain - defense-in-depth erosion of the append-only
    boundary ADR-010 relies on. Downgrades past 0003, checks the three
    application roles directly with `has_table_privilege` (not just
    `atp_worker` - the defect affected `atp_api` and `atp_paper_exec`
    identically), then re-upgrades to head so later tests in this session
    see the expected head state regardless of outcome."""
    try:
        _run_alembic(owner_dsn, "downgrade", "0002_seed_fixture_instruments")
        with owner_connection.cursor() as cur:
            for role in ("atp_api", "atp_paper_exec", "atp_worker"):
                for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                    cur.execute(
                        "SELECT has_table_privilege(%s, 'audit.audit_events', %s)",
                        (role, privilege),
                    )
                    (has_privilege,) = cur.fetchone()  # type: ignore[misc]
                    assert (
                        has_privilege is False
                    ), f"downgrade() must not grant {privilege} on audit.audit_events to {role}"
        owner_connection.rollback()
    finally:
        _run_alembic(owner_dsn, "upgrade", "head")
