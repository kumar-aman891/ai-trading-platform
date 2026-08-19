"""Phase 1 Step 6: append-only enforcement, proven at the trigger layer
(ADR-010) - not merely by grant absence (that's proven separately in
test_table_grants.py). A role with UPDATE/DELETE privilege (e.g. `atp_owner`
itself, which owns every table) must still be rejected by the trigger -
that is the whole point of a second, independent enforcement layer.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest


def _new_uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def seeded_audit_event_id(migrated_database: str, owner_connection: psycopg.Connection) -> str:
    event_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit.audit_events
                (event_id, correlation_id, occurred_at, recorded_at, actor_type, action)
            VALUES (%s, %s, now(), now(), 'SYSTEM', 'TEST_EVENT')
            """,
            (event_id, _new_uuid()),
        )
    owner_connection.commit()
    return event_id


def test_audit_events_reject_update_even_as_owner(
    owner_connection: psycopg.Connection, seeded_audit_event_id: str
) -> None:
    with pytest.raises(psycopg.errors.RaiseException), owner_connection.cursor() as cur:
        cur.execute(
            "UPDATE audit.audit_events SET action = 'TAMPERED' WHERE event_id = %s",
            (seeded_audit_event_id,),
        )
    owner_connection.rollback()


def test_audit_events_reject_delete_even_as_owner(
    owner_connection: psycopg.Connection, seeded_audit_event_id: str
) -> None:
    with pytest.raises(psycopg.errors.RaiseException), owner_connection.cursor() as cur:
        cur.execute("DELETE FROM audit.audit_events WHERE event_id = %s", (seeded_audit_event_id,))
    owner_connection.rollback()


def test_audit_events_api_role_has_no_update_or_delete_grant(
    migrated_database: str, api_connection: psycopg.Connection
) -> None:
    with api_connection.cursor() as cur:
        cur.execute("SELECT has_table_privilege(current_user, 'audit.audit_events', 'UPDATE')")
        (can_update,) = cur.fetchone()  # type: ignore[misc]
        cur.execute("SELECT has_table_privilege(current_user, 'audit.audit_events', 'DELETE')")
        (can_delete,) = cur.fetchone()  # type: ignore[misc]
    assert can_update is False
    assert can_delete is False


@pytest.fixture
def seeded_user_id(owner_connection: psycopg.Connection) -> str:
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
    return user_id


@pytest.fixture
def seeded_risk_config_id(owner_connection: psycopg.Connection, seeded_user_id: str) -> str:
    config_id = _new_uuid()
    version = int.from_bytes(os.urandom(4), "big")
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.risk_config
                (risk_config_id, mode, version, config, config_hash, active, created_at, created_by)
            VALUES (%s, 'PAPER', %s, '{"max_order_notional": "1000"}', %s, false, now(), %s)
            """,
            (config_id, version, _new_uuid(), seeded_user_id),
        )
    owner_connection.commit()
    return config_id


def test_risk_config_rejects_mutating_config_column(
    owner_connection: psycopg.Connection, seeded_risk_config_id: str
) -> None:
    with pytest.raises(psycopg.errors.RaiseException), owner_connection.cursor() as cur:
        cur.execute(
            'UPDATE core.risk_config SET config = \'{"max_order_notional": "999999"}\' '
            "WHERE risk_config_id = %s",
            (seeded_risk_config_id,),
        )
    owner_connection.rollback()


def test_risk_config_permits_toggling_active_flag(
    owner_connection: psycopg.Connection, seeded_risk_config_id: str
) -> None:
    """`active` is the one column the immutability trigger deliberately
    permits changing (docs/schemas/risk_config.md: "activating a new
    version inserts a row and flips `active`").

    The migration-seeded bootstrap PAPER config is already `active` when
    this test starts, and `uq_risk_config_one_active_per_mode` (a partial
    unique index) permits at most one active row per mode - so activating
    `seeded_risk_config_id` must first deactivate whatever else is active
    for `PAPER`, exactly as real activation logic would, or the UPDATE
    below raises `UniqueViolation`. First real run against Postgres
    (Phase 1 Step 12 Phase A) found this test had never actually
    exercised that constraint. The bootstrap row's `active` state is
    restored in `finally` so no later test in this session sees a
    PAPER mode with zero active configs."""
    with owner_connection.cursor() as cur:
        cur.execute(
            "UPDATE core.risk_config SET active = false WHERE mode = 'PAPER' AND active = true"
        )
        cur.execute(
            "UPDATE core.risk_config SET active = true WHERE risk_config_id = %s",
            (seeded_risk_config_id,),
        )
    owner_connection.commit()

    try:
        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT active FROM core.risk_config WHERE risk_config_id = %s",
                (seeded_risk_config_id,),
            )
            (active,) = cur.fetchone()  # type: ignore[misc]
        assert active is True
    finally:
        with owner_connection.cursor() as cur:
            cur.execute(
                "UPDATE core.risk_config SET active = false WHERE risk_config_id = %s",
                (seeded_risk_config_id,),
            )
            cur.execute(
                "UPDATE core.risk_config SET active = true "
                "WHERE mode = 'PAPER' AND created_by IS NULL AND version = 1"
            )
        owner_connection.commit()
