"""Step 5 database security item (g): role grants match the approved
matrix.

The "approved matrix" is ops/sql/roles_and_schemas.sql.tmpl itself - this
test re-derives what that file declares (independently, as a literal
Python constant below, not by reading the .sql file) and compares it
against what PostgreSQL actually has on record via `pg_default_acl`, so a
future edit to the bootstrap script that silently drifts from its own
documented intent is caught.
"""

from __future__ import annotations

import psycopg

# schema -> role -> sorted default table privileges, mirroring the
# `ALTER DEFAULT PRIVILEGES FOR ROLE atp_owner IN SCHEMA ... GRANT ...`
# statements in ops/sql/roles_and_schemas.sql.tmpl.
EXPECTED_DEFAULT_TABLE_PRIVILEGES: dict[str, dict[str, set[str]]] = {
    "core": {
        "atp_api": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "atp_paper_exec": {"SELECT"},
        "atp_worker": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    },
    "audit": {
        "atp_api": {"SELECT", "INSERT"},
        "atp_paper_exec": {"INSERT"},
        "atp_worker": {"SELECT", "INSERT"},
    },
    "paper": {
        "atp_api": {"SELECT", "INSERT"},
        "atp_paper_exec": {"SELECT", "INSERT", "UPDATE"},
        # atp_worker: absent - no USAGE on `paper` at all, so it cannot hold
        # any default table privilege there either.
    },
    "live": {
        # No application role receives any default privilege on `live`.
    },
}

FORBIDDEN_PRIVILEGES = {"TRUNCATE", "TRIGGER", "REFERENCES"}


def _actual_default_table_privileges(
    conn: psycopg.Connection,
) -> dict[str, dict[str, set[str]]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT n.nspname, r.rolname, a.privilege_type
            FROM pg_default_acl d
            JOIN pg_namespace n ON n.oid = d.defaclnamespace
            CROSS JOIN LATERAL aclexplode(d.defaclacl) a
            JOIN pg_roles r ON r.oid = a.grantee
            WHERE d.defaclobjtype = 'r'
              AND n.nspname = ANY(%s)
            """,
            (list(EXPECTED_DEFAULT_TABLE_PRIVILEGES),),
        )
        result: dict[str, dict[str, set[str]]] = {
            schema: {} for schema in EXPECTED_DEFAULT_TABLE_PRIVILEGES
        }
        for schema, role, privilege in cur.fetchall():
            result.setdefault(schema, {}).setdefault(role, set()).add(privilege)
        return result


def test_default_table_privileges_match_the_approved_matrix(
    owner_connection: psycopg.Connection,
) -> None:
    actual = _actual_default_table_privileges(owner_connection)
    for schema, expected_roles in EXPECTED_DEFAULT_TABLE_PRIVILEGES.items():
        actual_roles = actual.get(schema, {})
        # Only compare roles the matrix has an opinion about; atp_owner
        # itself is not compared (it is the object owner, not a grantee).
        for role, expected_privileges in expected_roles.items():
            assert actual_roles.get(role, set()) == expected_privileges, (
                f"{schema}.* default privileges for {role}: expected "
                f"{expected_privileges}, got {actual_roles.get(role, set())}"
            )
        # No unexpected role has any default privilege at all in this schema.
        unexpected_roles = set(actual_roles) - set(expected_roles)
        assert (
            not unexpected_roles
        ), f"{schema}.* grants a default privilege to unexpected role(s): {unexpected_roles}"


def test_no_role_ever_receives_truncate_trigger_or_references_by_default(
    owner_connection: psycopg.Connection,
) -> None:
    actual = _actual_default_table_privileges(owner_connection)
    for schema, roles in actual.items():
        for role, privileges in roles.items():
            overreach = privileges & FORBIDDEN_PRIVILEGES
            assert not overreach, f"{schema}.* grants {role} {overreach} - not part of the matrix"


def test_no_role_ever_receives_update_or_delete_on_audit_by_default(
    owner_connection: psycopg.Connection,
) -> None:
    """Append-only enforcement (ADR-010) starts here: nothing in the
    default-privilege matrix ever grants UPDATE or DELETE on `audit` to any
    role - the migration step's explicit REVOKE + trigger (Step 6+) is a
    second, independent layer on top of this, never the only one."""
    actual = _actual_default_table_privileges(owner_connection)
    for role, privileges in actual.get("audit", {}).items():
        assert "UPDATE" not in privileges, f"audit.* must never grant UPDATE to {role}"
        assert "DELETE" not in privileges, f"audit.* must never grant DELETE to {role}"
