"""Phase 1 Step 14 (ADR-007): kill-switch engage/disengage against a real,
migrated database, using the actual `atp_api` role.

`tests/unit/api/test_kill_switches.py` already exercises the route/service
logic extensively against in-memory fakes (RBAC asymmetry, CSRF,
GLOBAL_LIVE/LIVE_ACCOUNT rejection, idempotency, audit/history
orchestration); this file's job is narrower and is what no fake can prove:

- that `SqlAlchemyKillSwitchStateRepository.apply_transition` genuinely
  round-trips through `core.kill_switch_state`/`core.kill_switch_history`
  under the real `atp_api` role's grants (`SELECT, INSERT, UPDATE` on the
  former, `SELECT, INSERT` on the latter - `0003_table_grants.py`), for
  both the update-an-existing-row path (`PAPER`) and the insert-on-first
  -touch path (`STRATEGY:{id}`/`INSTRUMENT:{id}`);
- that `core.kill_switch_state`'s `reason_required` CHECK
  (`updated_by IS NULL OR reason IS NOT NULL`) is genuinely satisfied by
  what the ORM path writes, not merely assumed compatible with it
  (`tests/integration/db/test_table_constraints.py::
  test_kill_switch_state_requires_reason_when_updated_by_is_set` already
  proves the constraint rejects a bad write via raw SQL; this file proves
  a real write through the actual code path never produces one); and
- that `core.kill_switch_history` is genuinely append-only under the real
  trigger, closing a gap `test_table_grants.py`'s existing
  `test_kill_switch_history_never_grants_update_or_delete_to_any_role`
  left open: a grant check proves no role *has* UPDATE/DELETE privilege;
  it does not prove the trigger itself still fires for a role that
  somehow attempted one anyway (`atp_owner`, in
  `test_audit_immutability.py`'s established pattern for the structurally
  identical `audit.audit_events` trigger).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from atp_persistence.db import make_session_factory
from atp_persistence.repositories.kill_switches import SqlAlchemyKillSwitchStateRepository
from tests.integration.db.conftest import delete_user_cascade


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_async_psycopg_url(dsn: str) -> str:
    """Mirrors `tests/integration/db/test_paper_execution_gateway.py`'s
    own copy of this helper - no shared module exists for it."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


@pytest.fixture
def seeded_changed_by_user(migrated_database: str, owner_connection: psycopg.Connection):
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
    delete_user_cascade(owner_connection, user_id)


def _reset_paper_switch(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE core.kill_switch_state SET engaged = false, updated_at = now(), "
            "updated_by = NULL, reason = NULL WHERE switch_id = 'PAPER'"
        )
    conn.commit()


def _delete_history_for(conn: psycopg.Connection, switch_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.kill_switch_history WHERE switch_id = %s", (switch_id,))
    conn.commit()


def test_apply_transition_updates_an_existing_row_via_the_real_atp_api_role(
    migrated_database: str,
    owner_dsn: str,
    api_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_changed_by_user: str,
) -> None:
    """`PAPER` is migration-seeded (`0001_core_audit_paper_schema.py`), so
    this exercises `apply_transition`'s UPDATE branch specifically."""
    _reset_paper_switch(owner_connection)
    now = datetime.now(UTC)

    async def run() -> object:
        engine = create_async_engine(_as_async_psycopg_url(api_dsn))
        try:
            async with make_session_factory(engine)() as session, session.begin():
                repo = SqlAlchemyKillSwitchStateRepository(session)
                return await repo.apply_transition(
                    "PAPER",
                    new_engaged=True,
                    changed_by=seeded_changed_by_user,
                    reason="integration test engage",
                    now=now,
                    history_id=_new_uuid(),
                    audit_event_id=_new_uuid(),
                )
        finally:
            await engine.dispose()

    try:
        result = asyncio.run(run())
        assert result.engaged is True

        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT engaged, updated_by, reason FROM core.kill_switch_state "
                "WHERE switch_id = 'PAPER'"
            )
            state_row = cur.fetchone()
            cur.execute(
                "SELECT previous_engaged, new_engaged, changed_by, reason "
                "FROM core.kill_switch_history WHERE switch_id = 'PAPER' "
                "ORDER BY changed_at DESC LIMIT 1"
            )
            history_row = cur.fetchone()
        owner_connection.rollback()

        assert state_row is not None
        assert state_row[0] is True
        assert state_row[1] == seeded_changed_by_user
        assert state_row[2] == "integration test engage"

        assert history_row is not None
        assert history_row[0] is False  # previous_engaged
        assert history_row[1] is True  # new_engaged
        assert history_row[2] == seeded_changed_by_user
        assert history_row[3] == "integration test engage"
    finally:
        _reset_paper_switch(owner_connection)
        _delete_history_for(owner_connection, "PAPER")


def test_apply_transition_creates_a_strategy_switch_on_first_touch(
    migrated_database: str,
    owner_dsn: str,
    api_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_changed_by_user: str,
) -> None:
    """`STRATEGY:{id}` carries no migration seed row - this exercises
    `apply_transition`'s INSERT branch, and confirms `previous_engaged` is
    recorded as `False` for a switch that did not exist a moment ago
    (documented interpretation, `SqlAlchemyKillSwitchStateRepository.
    apply_transition`'s own docstring)."""
    switch_id = f"STRATEGY:it-{_new_uuid()}"
    now = datetime.now(UTC)

    async def run() -> object:
        engine = create_async_engine(_as_async_psycopg_url(api_dsn))
        try:
            async with make_session_factory(engine)() as session, session.begin():
                repo = SqlAlchemyKillSwitchStateRepository(session)
                return await repo.apply_transition(
                    switch_id,
                    new_engaged=True,
                    changed_by=seeded_changed_by_user,
                    reason="integration test first engage",
                    now=now,
                    history_id=_new_uuid(),
                    audit_event_id=_new_uuid(),
                )
        finally:
            await engine.dispose()

    try:
        result = asyncio.run(run())
        assert result.engaged is True

        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT previous_engaged, new_engaged FROM core.kill_switch_history "
                "WHERE switch_id = %s",
                (switch_id,),
            )
            history_row = cur.fetchone()
        owner_connection.rollback()

        assert history_row is not None
        assert history_row[0] is False
        assert history_row[1] is True
    finally:
        with owner_connection.cursor() as cur:
            cur.execute("DELETE FROM core.kill_switch_history WHERE switch_id = %s", (switch_id,))
            cur.execute("DELETE FROM core.kill_switch_state WHERE switch_id = %s", (switch_id,))
        owner_connection.commit()


def test_kill_switch_history_rejects_update_even_as_owner(
    migrated_database: str, owner_connection: psycopg.Connection, seeded_changed_by_user: str
) -> None:
    history_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.kill_switch_history
                (history_id, switch_id, previous_engaged, new_engaged, changed_at,
                 changed_by, reason, audit_event_id)
            VALUES (%s, 'PAPER', false, true, now(), %s, 'fixture', %s)
            """,
            (history_id, seeded_changed_by_user, _new_uuid()),
        )
    owner_connection.commit()

    try:
        with pytest.raises(psycopg.errors.RaiseException), owner_connection.cursor() as cur:
            cur.execute(
                "UPDATE core.kill_switch_history SET reason = 'TAMPERED' WHERE history_id = %s",
                (history_id,),
            )
        owner_connection.rollback()
    finally:
        with owner_connection.cursor() as cur:
            cur.execute("DELETE FROM core.kill_switch_history WHERE history_id = %s", (history_id,))
        owner_connection.commit()


def test_kill_switch_history_rejects_delete_even_as_owner(
    migrated_database: str, owner_connection: psycopg.Connection, seeded_changed_by_user: str
) -> None:
    history_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.kill_switch_history
                (history_id, switch_id, previous_engaged, new_engaged, changed_at,
                 changed_by, reason, audit_event_id)
            VALUES (%s, 'PAPER', false, true, now(), %s, 'fixture', %s)
            """,
            (history_id, seeded_changed_by_user, _new_uuid()),
        )
    owner_connection.commit()

    try:
        with pytest.raises(psycopg.errors.RaiseException), owner_connection.cursor() as cur:
            cur.execute("DELETE FROM core.kill_switch_history WHERE history_id = %s", (history_id,))
        owner_connection.rollback()
    finally:
        with owner_connection.cursor() as cur:
            cur.execute("DELETE FROM core.kill_switch_history WHERE history_id = %s", (history_id,))
        owner_connection.commit()
