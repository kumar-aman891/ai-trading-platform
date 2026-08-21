"""Step 5 database security items (d), (e): no LIVE execution role exists,
and no application role is superuser."""

from __future__ import annotations

import psycopg

#: Every `atp_*` role `ops/sql/roles_and_schemas.sql.tmpl` creates.
#: `atp_strategy` joined this list in Strategy Framework Milestone 2B
#: (ADR-014/ADR-015); adding it here is not bookkeeping - it extends this
#: module's no-SUPERUSER and no-CREATEDB/CREATEROLE assertions to cover
#: the new role too, and keeps
#: `test_exactly_the_expected_application_roles_exist` a genuine
#: "no unexpected role was created" check rather than a stale literal.
APPLICATION_ROLES = ("atp_owner", "atp_api", "atp_paper_exec", "atp_worker", "atp_strategy")


def test_no_application_role_is_superuser(owner_connection: psycopg.Connection) -> None:
    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT rolname FROM pg_roles WHERE rolsuper AND rolname = ANY(%s)",
            (list(APPLICATION_ROLES),),
        )
        assert cur.fetchall() == [], "no atp_* role may hold SUPERUSER"


def test_no_application_role_can_create_databases_or_roles(
    owner_connection: psycopg.Connection,
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT rolname, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = ANY(%s)",
            (list(APPLICATION_ROLES),),
        )
        rows = cur.fetchall()
        assert len(rows) == len(APPLICATION_ROLES)
        for rolname, rolcreatedb, rolcreaterole in rows:
            assert rolcreatedb is False, f"{rolname} must not have CREATEDB"
            assert rolcreaterole is False, f"{rolname} must not have CREATEROLE"


def test_no_live_execution_role_exists(owner_connection: psycopg.Connection) -> None:
    """No `atp_exec_live` / LIVE-scoped database role exists in Phase 1
    (ADR-005, ADR-008) - mirrors the process-level guarantee in
    tests/safety/test_no_live_execution.py at the database layer."""
    with owner_connection.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname ILIKE %s", ("%live%",))
        assert cur.fetchall() == []


def test_exactly_the_expected_application_roles_exist(
    owner_connection: psycopg.Connection,
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE rolname LIKE 'atp_%' ORDER BY rolname")
        found = {row[0] for row in cur.fetchall()}
        assert found == set(APPLICATION_ROLES)
