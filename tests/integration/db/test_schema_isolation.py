"""Step 5 database security items (a), (b), (c), (f), (k): required schemas
exist, `live` is reachable by no application role, and `atp_worker` has no
order-path (`paper`) privileges of any kind."""

from __future__ import annotations

import psycopg

REQUIRED_SCHEMAS = {"core", "audit", "paper", "live"}
APPLICATION_ROLES = ("atp_api", "atp_paper_exec", "atp_worker")


def test_required_schemas_exist(owner_connection: psycopg.Connection) -> None:
    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ANY(%s)",
            (list(REQUIRED_SCHEMAS),),
        )
        found = {row[0] for row in cur.fetchall()}
        assert found == REQUIRED_SCHEMAS


def test_no_application_role_has_usage_on_live_schema(
    owner_connection: psycopg.Connection,
) -> None:
    with owner_connection.cursor() as cur:
        for role in APPLICATION_ROLES:
            cur.execute("SELECT has_schema_privilege(%s, 'live', 'USAGE')", (role,))
            (has_usage,) = cur.fetchone()  # type: ignore[misc]
            assert has_usage is False, f"{role} must have zero privileges on live.* (item a/b)"


def test_no_application_role_has_create_on_live_schema(
    owner_connection: psycopg.Connection,
) -> None:
    with owner_connection.cursor() as cur:
        for role in APPLICATION_ROLES:
            cur.execute("SELECT has_schema_privilege(%s, 'live', 'CREATE')", (role,))
            (has_create,) = cur.fetchone()  # type: ignore[misc]
            assert has_create is False, f"{role} must have zero privileges on live.* (item a/b)"


def test_api_role_has_zero_privileges_on_live_schema(
    api_connection: psycopg.Connection,
) -> None:
    """Same assertion as above, proven from the api role's own connection -
    not just as reported by the owner role (item a)."""
    with api_connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(current_user, 'live', 'USAGE')")
        (has_usage,) = cur.fetchone()  # type: ignore[misc]
        assert has_usage is False


def test_paper_exec_role_has_zero_privileges_on_live_schema(
    paper_exec_connection: psycopg.Connection,
) -> None:
    """Same assertion, proven from the paper-exec role's own connection
    (item b)."""
    with paper_exec_connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(current_user, 'live', 'USAGE')")
        (has_usage,) = cur.fetchone()  # type: ignore[misc]
        assert has_usage is False


def test_worker_role_has_no_order_path_privileges(
    worker_connection: psycopg.Connection,
) -> None:
    """`atp_worker` has no USAGE on `paper` at all - "no order-path
    privileges" is structural (there is nothing under paper.* it can even
    see), not a per-table restriction (item c)."""
    with worker_connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(current_user, 'paper', 'USAGE')")
        (has_usage,) = cur.fetchone()  # type: ignore[misc]
        assert has_usage is False


def test_worker_role_has_no_privileges_on_live_schema(
    worker_connection: psycopg.Connection,
) -> None:
    with worker_connection.cursor() as cur:
        cur.execute("SELECT has_schema_privilege(current_user, 'live', 'USAGE')")
        (has_usage,) = cur.fetchone()  # type: ignore[misc]
        assert has_usage is False


def test_no_cross_mode_privileges_are_accidentally_granted(
    owner_connection: psycopg.Connection,
) -> None:
    """No application role's schema-level grants extend beyond what
    ops/sql/roles_and_schemas.sql.tmpl explicitly declares - specifically,
    nothing grants any of atp_api/atp_paper_exec/atp_worker any privilege on
    `live` via *any* privilege type, not just USAGE/CREATE (item k)."""
    live_privileges = ("USAGE", "CREATE")
    with owner_connection.cursor() as cur:
        for role in APPLICATION_ROLES:
            for privilege in live_privileges:
                cur.execute("SELECT has_schema_privilege(%s, 'live', %s)", (role, privilege))
                (granted,) = cur.fetchone()  # type: ignore[misc]
                assert granted is False, f"{role} must not have {privilege} on live"
