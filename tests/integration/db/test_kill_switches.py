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
  both the update-an-existing-row path and the insert-on-first-touch path
  (`STRATEGY:{id}`/`INSTRUMENT:{id}`);
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

Two cleanup rules govern every test below, both learned from this module's
own first real CI run (the integration job was red from the day this file
landed in `5489851` until the CI-baseline reconciliation milestone):

1. **Nothing here ever deletes from `core.kill_switch_history`.** It is
   append-only; the trigger raises for every role including `atp_owner` -
   which is the very property two of these tests assert. Teardown that
   attempts it does not merely fail, it aborts the shared session-scoped
   `owner_connection` mid-transaction and takes unrelated later tests down
   with it. This mirrors `delete_user_cascade`'s own standing note about
   `audit.audit_events` (tests/integration/db/conftest.py).
2. **No test writes history against a migration-seeded switch id.** Those
   four rows are deleted *by predicate* in migration 0001's `downgrade()`,
   and a permanent history row holding an FK to one of them breaks
   `alembic downgrade base` for the rest of the session. Use
   `existing_test_switch`/`new_test_switch_id()` instead; rows they leave
   behind are removed by `DROP TABLE`, not by that predicate.
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
from tests.integration.db.conftest import delete_user_cascade, new_test_switch_id


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


def _apply_transition(
    api_dsn: str, switch_id: str, *, changed_by: str, reason: str, now: datetime
) -> object:
    """One `apply_transition` through a real `atp_api` connection."""

    async def run() -> object:
        engine = create_async_engine(_as_async_psycopg_url(api_dsn))
        try:
            async with make_session_factory(engine)() as session, session.begin():
                repo = SqlAlchemyKillSwitchStateRepository(session)
                return await repo.apply_transition(
                    switch_id,
                    new_engaged=True,
                    changed_by=changed_by,
                    reason=reason,
                    now=now,
                    history_id=_new_uuid(),
                    audit_event_id=_new_uuid(),
                )
        finally:
            await engine.dispose()

    return asyncio.run(run())


def test_apply_transition_updates_an_existing_row_via_the_real_atp_api_role(
    migrated_database: str,
    api_dsn: str,
    owner_connection: psycopg.Connection,
    existing_test_switch: str,
    seeded_changed_by_user: str,
) -> None:
    """`existing_test_switch` is owner-inserted before the test starts, so
    this exercises `apply_transition`'s UPDATE branch specifically - the
    branch is selected by the row *existing*, which is why a non-seeded
    switch proves it exactly as well as `PAPER` did, without this test
    mutating migration-owned state or pinning a seed row with permanent
    history (see this module's docstring, rule 2)."""
    now = datetime.now(UTC)

    result = _apply_transition(
        api_dsn,
        existing_test_switch,
        changed_by=seeded_changed_by_user,
        reason="integration test engage",
        now=now,
    )
    assert result.engaged is True

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT engaged, updated_by, reason FROM core.kill_switch_state WHERE switch_id = %s",
            (existing_test_switch,),
        )
        state_row = cur.fetchone()
        cur.execute(
            "SELECT previous_engaged, new_engaged, changed_by, reason "
            "FROM core.kill_switch_history WHERE switch_id = %s "
            "ORDER BY changed_at DESC LIMIT 1",
            (existing_test_switch,),
        )
        history_row = cur.fetchone()
    owner_connection.rollback()

    assert state_row is not None
    assert state_row[0] is True
    # `str(...)`: raw psycopg maps a Postgres `uuid` column to a Python
    # `UUID` object, so a bare `==` against the `str` fixture value is
    # always False. Same normalization as test_risk_config_bootstrap.py.
    assert str(state_row[1]) == seeded_changed_by_user
    assert state_row[2] == "integration test engage"

    assert history_row is not None
    assert history_row[0] is False  # previous_engaged
    assert history_row[1] is True  # new_engaged
    assert str(history_row[2]) == seeded_changed_by_user
    assert history_row[3] == "integration test engage"


def test_apply_transition_creates_a_strategy_switch_on_first_touch(
    migrated_database: str,
    api_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_changed_by_user: str,
) -> None:
    """`STRATEGY:{id}` carries no migration seed row - this exercises
    `apply_transition`'s INSERT branch, and confirms `previous_engaged` is
    recorded as `False` for a switch that did not exist a moment ago
    (documented interpretation, `SqlAlchemyKillSwitchStateRepository.
    apply_transition`'s own docstring).

    This is also the regression test for the FK ordering bug that branch
    carried until the CI-baseline reconciliation milestone: both rows were
    added to the session and flushed together, and with no `relationship()`
    declared anywhere in `atp_persistence.models` the unit of work emitted
    the history INSERT first, violating
    `fk_kill_switch_history_switch_id_kill_switch_state`. It made the
    first-ever transition of every `STRATEGY:{id}`/`INSTRUMENT:{id}` switch
    fail - the exact mechanism by which an administrator enables a strategy
    (ADR-014) - and no in-memory fake could have caught it.

    Both rows are deliberately left behind: the history row is append-only
    and the state row is FK-pinned by it. Neither is migration-seeded, so
    `downgrade base` removes them by `DROP TABLE` (see module docstring).
    """
    switch_id = new_test_switch_id()
    now = datetime.now(UTC)

    result = _apply_transition(
        api_dsn,
        switch_id,
        changed_by=seeded_changed_by_user,
        reason="integration test first engage",
        now=now,
    )
    assert result.engaged is True

    with owner_connection.cursor() as cur:
        cur.execute("SELECT engaged FROM core.kill_switch_state WHERE switch_id = %s", (switch_id,))
        state_row = cur.fetchone()
        cur.execute(
            "SELECT previous_engaged, new_engaged FROM core.kill_switch_history "
            "WHERE switch_id = %s",
            (switch_id,),
        )
        history_row = cur.fetchone()
    owner_connection.rollback()

    assert state_row is not None
    assert state_row[0] is True

    assert history_row is not None
    assert history_row[0] is False
    assert history_row[1] is True


def test_kill_switch_history_rejects_update_even_as_owner(
    migrated_database: str,
    owner_connection: psycopg.Connection,
    existing_test_switch: str,
    seeded_changed_by_user: str,
) -> None:
    """The inserted row is deliberately never cleaned up: it cannot be, and
    that inability *is* the property under test (module docstring, rule 1)."""
    history_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.kill_switch_history
                (history_id, switch_id, previous_engaged, new_engaged, changed_at,
                 changed_by, reason, audit_event_id)
            VALUES (%s, %s, false, true, now(), %s, 'fixture', %s)
            """,
            (history_id, existing_test_switch, seeded_changed_by_user, _new_uuid()),
        )
    owner_connection.commit()

    with pytest.raises(psycopg.errors.RaiseException), owner_connection.cursor() as cur:
        cur.execute(
            "UPDATE core.kill_switch_history SET reason = 'TAMPERED' WHERE history_id = %s",
            (history_id,),
        )
    owner_connection.rollback()


def test_kill_switch_history_rejects_delete_even_as_owner(
    migrated_database: str,
    owner_connection: psycopg.Connection,
    existing_test_switch: str,
    seeded_changed_by_user: str,
) -> None:
    """As above: the row survives the test, because the trigger this test
    asserts is exactly what prevents removing it."""
    history_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.kill_switch_history
                (history_id, switch_id, previous_engaged, new_engaged, changed_at,
                 changed_by, reason, audit_event_id)
            VALUES (%s, %s, false, true, now(), %s, 'fixture', %s)
            """,
            (history_id, existing_test_switch, seeded_changed_by_user, _new_uuid()),
        )
    owner_connection.commit()

    with pytest.raises(psycopg.errors.RaiseException), owner_connection.cursor() as cur:
        cur.execute("DELETE FROM core.kill_switch_history WHERE history_id = %s", (history_id,))
    owner_connection.rollback()
