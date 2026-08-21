"""Step 6 architecture reconciliation, Fix 1: `core.risk_config` bootstrap
behavior against a real database.

docs/schemas/risk_config.md calls for "one migration-seeded PAPER config
row"; docs/schemas/user.md forbids seeding any `core.users` row, ever.
Resolved by making `core.risk_config.created_by` nullable and seeding the
bootstrap row with `created_by = NULL`, mirroring
`core.kill_switch_state.updated_by`'s existing NULL-for-system convention -
proven here, not merely asserted in a docstring.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from tests.integration.db.conftest import delete_user_cascade


def _new_uuid() -> str:
    return str(uuid.uuid4())


def test_clean_migration_creates_exactly_one_bootstrap_risk_config(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT risk_config_id, mode, version, active, created_by "
            "FROM core.risk_config WHERE created_by IS NULL"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, f"expected exactly one migration-seeded row, found {len(rows)}"
    (risk_config_id, mode, version, active, created_by) = rows[0]
    assert mode == "PAPER"
    assert version == 1
    assert active is True
    assert created_by is None
    assert risk_config_id is not None


def test_bootstrap_config_has_created_by_null(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT created_by FROM core.risk_config WHERE mode = 'PAPER' AND version = 1")
        (created_by,) = cur.fetchone()  # type: ignore[misc]
    assert created_by is None


def test_no_seeded_core_users_row_exists(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    """docs/schemas/user.md: "No default/seeded user in any migration."
    The risk_config bootstrap fix must not have introduced one."""
    with owner_connection.cursor() as cur:
        cur.execute("SELECT username FROM core.users ORDER BY username LIMIT 10")
        usernames = [row[0] for row in cur.fetchall()]
    # Names the rows rather than reporting a bare count: every historical
    # failure of this assertion has been a *test fixture* that leaked its
    # own `fixture-{uuid}` user (a cleanup teardown that died on an aborted
    # transaction), not a migration that seeded one - and a count alone
    # cannot tell those two apart.
    assert usernames == [], (
        f"core.users must be empty - docs/schemas/user.md: 'No default/seeded user in any "
        f"migration.' Found {usernames}; a 'fixture-*' name here means a test fixture leaked "
        f"its user rather than that a migration seeded one."
    )


@pytest.fixture
def seeded_user_id(owner_connection: psycopg.Connection) -> Iterator[str]:
    user_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.users (user_id, username, password_hash, role, created_at, updated_at)
            VALUES (%s, %s, 'argon2id$fixture', 'administrator', now(), now())
            """,
            (user_id, f"fixture-{user_id}"),
        )
    owner_connection.commit()
    yield user_id
    # The shared FK-ordered helper: `core.risk_config.created_by`
    # references `core.users`, and the tests below deliberately create
    # such a row, so a bare `DELETE FROM core.users` raises
    # `ForeignKeyViolation` (Phase 1 Step 12 Phase A).
    delete_user_cascade(owner_connection, user_id)


def test_application_created_config_can_carry_a_non_null_creator(
    migrated_database: str, owner_connection: psycopg.Connection, seeded_user_id: str
) -> None:
    """The nullable `created_by` column does not weaken the requirement
    that a *real*, application-driven version change names an
    administrator - it only exempts the one migration-seeded bootstrap
    row."""
    risk_config_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.risk_config
                (risk_config_id, mode, version, config, config_hash, active, created_at, created_by)
            VALUES (%s, 'PAPER', 2, '{}', %s, false, now(), %s)
            """,
            (risk_config_id, _new_uuid(), seeded_user_id),
        )
    owner_connection.commit()

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT created_by FROM core.risk_config WHERE risk_config_id = %s", (risk_config_id,)
        )
        (created_by,) = cur.fetchone()  # type: ignore[misc]
    # `str(...)`: raw psycopg maps a Postgres `uuid` column to a Python
    # `UUID` object; see the same normalization in
    # test_paper_proposal_intake.py.
    assert str(created_by) == seeded_user_id

    with owner_connection.cursor() as cur:
        cur.execute("DELETE FROM core.risk_config WHERE risk_config_id = %s", (risk_config_id,))
    owner_connection.commit()


def test_bootstrap_config_created_by_remains_immutable_once_set(
    migrated_database: str, owner_connection: psycopg.Connection, seeded_user_id: str
) -> None:
    """The risk_config_immutable trigger (migration 0001) must reject
    changing `created_by` away from its seeded NULL, exactly as it rejects
    changing any other immutable column - nullability does not create an
    exception to immutability."""
    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT risk_config_id FROM core.risk_config WHERE mode = 'PAPER' AND version = 1"
        )
        (risk_config_id,) = cur.fetchone()  # type: ignore[misc]

    with pytest.raises(psycopg.errors.RaiseException), owner_connection.cursor() as cur:
        cur.execute(
            "UPDATE core.risk_config SET created_by = %s WHERE risk_config_id = %s",
            (seeded_user_id, risk_config_id),
        )
    owner_connection.rollback()


def test_bootstrap_config_active_flag_remains_toggleable(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    """Confirms the reconciliation did not accidentally touch the
    trigger's one deliberate exception (`active`), covered generally in
    test_audit_immutability.py's risk_config tests; repeated here directly
    against the bootstrap row itself."""
    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT risk_config_id, active FROM core.risk_config WHERE mode = 'PAPER' AND version = 1"
        )
        (risk_config_id, active) = cur.fetchone()  # type: ignore[misc]

    with owner_connection.cursor() as cur:
        cur.execute(
            "UPDATE core.risk_config SET active = %s WHERE risk_config_id = %s",
            (not active, risk_config_id),
        )
    owner_connection.commit()

    with owner_connection.cursor() as cur:
        cur.execute(
            "UPDATE core.risk_config SET active = %s WHERE risk_config_id = %s",
            (active, risk_config_id),
        )
    owner_connection.commit()
