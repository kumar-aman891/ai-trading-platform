"""Phase 1 Step 12 Phase B (ADR-013): `atp_worker` against a real, migrated
database, using the actual `atp_worker` role.

`tests/unit/worker/` already exercises the claim protocol, lease sweep,
scheduler arithmetic, and all three handlers extensively against in-memory
fakes; this file's job is the one no fake can do (ADR-013's own Context
section, and the "column-scoped-SELECT trap" identified during Step 12
Phase A reconnaissance):

- that two genuinely concurrent claimants over separate connections really
  do resolve to exactly one winner under `SELECT ... FOR UPDATE SKIP
  LOCKED`, not merely that the repository *asks* for that locking mode
  (`tests/unit/worker/test_runner.py` proves the latter with a fake that
  does not implement row locking at all);
- that a lease genuinely expires and is reclaimed through the real
  `atp_worker` role's grants;
- that `enqueue_if_absent` colliding a second time is the *database's*
  `ux_job_queue_one_live_per_type` partial unique index at work, not an
  artifact of the fake's `set[str]` in `tests/unit/worker/fakes.py`;
- that `SqlAlchemyWorkerSessionObservationRepository`'s narrow, three
  -column `select(...)` succeeds under the real `atp_worker` role while
  `SqlAlchemySessionRepository.get_by_hash`'s `select(SessionRow)` -
  requesting all seven columns - genuinely raises `InsufficientPrivilege`
  under that same role. This is the one property in ADR-013's Context
  section explicitly called out as invisible to any unit test with a
  fake; and
- one full claim -> handler -> terminal-state lifecycle through the real
  three-transaction protocol (`atp_worker.runner.run_once`) and the real
  `atp_worker` role, exercising the production `HANDLER_REGISTRY`
  end-to-end rather than a registry built by the test.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
import sqlalchemy.exc
from sqlalchemy.ext.asyncio import create_async_engine

from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_persistence.db import make_session_factory
from atp_persistence.repositories.jobs import SqlAlchemyJobQueueRepository
from atp_persistence.repositories.session_observations import (
    SqlAlchemyWorkerSessionObservationRepository,
)
from atp_persistence.repositories.sessions import SqlAlchemySessionRepository
from atp_worker.runner import run_once, sweep_expired_leases
from atp_worker.uow import worker_unit_of_work_factory
from tests.integration.db.conftest import delete_user_cascade


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_async_psycopg_url(dsn: str) -> str:
    """Mirrors `tests/integration/db/test_paper_execution_gateway.py`'s
    own copy of this helper - no shared module exists for it, matching
    that file's existing precedent rather than introducing one."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


def _insert_job(
    conn: psycopg.Connection,
    *,
    job_id: str,
    job_type: str,
    status: str = "PENDING",
    attempts: int = 0,
    max_attempts: int = 3,
    locked_at_ago: timedelta | None = None,
    locked_by: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.job_queue
                (job_id, job_type, payload, status, attempts, max_attempts,
                 scheduled_for, locked_at, locked_by, created_at)
            VALUES
                (%s, %s, '{}', %s, %s, %s, now(),
                 CASE WHEN %s::interval IS NULL THEN NULL ELSE now() - %s::interval END,
                 %s, now())
            """,
            (
                job_id,
                job_type,
                status,
                attempts,
                max_attempts,
                locked_at_ago,
                locked_at_ago,
                locked_by,
            ),
        )
    conn.commit()


def _delete_job(conn: psycopg.Connection, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.job_queue WHERE job_id = %s", (job_id,))
    conn.commit()


def _fetch_job(conn: psycopg.Connection, job_id: str) -> tuple[object, ...] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, attempts, locked_at, locked_by, scheduled_for, completed_at "
            "FROM core.job_queue WHERE job_id = %s",
            (job_id,),
        )
        row = cur.fetchone()
    conn.rollback()
    return row


# --- concurrent claim exclusivity (ADR-013 Section 3) ----------------------


def test_concurrent_run_once_over_a_single_due_job_admits_exactly_one_claimant(
    migrated_database: str,
    owner_dsn: str,
    worker_dsn: str,
    owner_connection: psycopg.Connection,
) -> None:
    """Two genuinely concurrent `run_once` calls, each over its own engine
    and connection, against a single `SESSION_REAP` row - the real proof
    that `SELECT ... FOR UPDATE SKIP LOCKED` (not merely a repository
    method that claims to use it) resolves to exactly one claimant.
    `SESSION_REAP` is chosen because its handler makes no additional
    writes beyond the job row itself, keeping this test's assertions
    about exactly what the *claim* protocol did rather than any
    handler-specific side effect."""
    job_id = _new_uuid()
    _insert_job(owner_connection, job_id=job_id, job_type="SESSION_REAP")

    async def run() -> list[bool]:
        engine_a = create_async_engine(_as_async_psycopg_url(worker_dsn))
        engine_b = create_async_engine(_as_async_psycopg_url(worker_dsn))
        try:
            return await asyncio.gather(
                run_once(
                    worker_unit_of_work_factory(make_session_factory(engine_a)),
                    clock=UTCClock(),
                    id_generator=UUIDv7Generator(),
                    instance_id="worker-a",
                ),
                run_once(
                    worker_unit_of_work_factory(make_session_factory(engine_b)),
                    clock=UTCClock(),
                    id_generator=UUIDv7Generator(),
                    instance_id="worker-b",
                ),
            )
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

    try:
        claimed_flags = asyncio.run(run())
        assert claimed_flags.count(True) == 1
        assert claimed_flags.count(False) == 1

        row = _fetch_job(owner_connection, job_id)
        assert row is not None
        status, attempts, locked_at, locked_by, _scheduled_for, completed_at = row
        assert status == "SUCCEEDED"
        assert attempts == 1  # claimed exactly once, never double-claimed
        assert locked_at is None
        assert locked_by is None
        assert completed_at is not None
    finally:
        _delete_job(owner_connection, job_id)


# --- lease reclaim (ADR-013 Section 5) --------------------------------------


def test_sweep_expired_leases_reclaims_a_stuck_running_job_via_the_worker_role(
    migrated_database: str,
    owner_dsn: str,
    worker_dsn: str,
    owner_connection: psycopg.Connection,
) -> None:
    """A `RUNNING` row whose `locked_at` is older than
    `LEASE_DURATION_SECONDS` (300s) is functionally a crashed claim -
    `sweep_expired_leases`, run through the real `atp_worker` role, must
    reclaim it back to `PENDING` with a future `scheduled_for`, exactly as
    the fake-backed `tests/unit/worker/test_runner.py::
    test_sweep_expired_leases_*` tests already prove against a fake that
    does not model row locking or `locked_at` staleness at the database
    level at all."""
    job_id = _new_uuid()
    _insert_job(
        owner_connection,
        job_id=job_id,
        job_type="RETENTION",
        status="RUNNING",
        attempts=1,
        max_attempts=3,
        locked_at_ago=timedelta(minutes=10),
        locked_by="fixture-crashed-worker",
    )

    async def run() -> int:
        engine = create_async_engine(_as_async_psycopg_url(worker_dsn))
        try:
            uow_factory = worker_unit_of_work_factory(make_session_factory(engine))
            return await sweep_expired_leases(uow_factory, clock=UTCClock())
        finally:
            await engine.dispose()

    try:
        reclaimed_count = asyncio.run(run())
        assert reclaimed_count == 1

        row = _fetch_job(owner_connection, job_id)
        assert row is not None
        status, attempts, locked_at, locked_by, scheduled_for, completed_at = row
        assert status == "PENDING"
        assert attempts == 1  # unchanged - only claim_next increments attempts
        assert locked_at is None
        assert locked_by is None
        assert completed_at is None
        assert scheduled_for > datetime.now(UTC)  # backoff pushed it into the future
    finally:
        _delete_job(owner_connection, job_id)


# --- duplicate live-job rejection (ADR-013 Section 6) -----------------------


def test_enqueue_if_absent_rejects_a_duplicate_live_job_via_the_worker_role(
    migrated_database: str,
    owner_dsn: str,
    worker_dsn: str,
    owner_connection: psycopg.Connection,
) -> None:
    """The app-level counterpart to `tests/integration/db/
    test_table_constraints.py::test_job_queue_rejects_a_second_pending_job_
    of_the_same_type`, which proves the same invariant via a raw `INSERT`.
    Here the actual production code path - `SqlAlchemyJobQueueRepository.
    enqueue_if_absent`, the one method `scheduler.py` calls - is what
    receives the collision and reports it as `False`, under the real
    `atp_worker` role rather than `atp_owner`."""
    job_id_a, job_id_b = _new_uuid(), _new_uuid()

    async def run() -> tuple[bool, bool]:
        engine = create_async_engine(_as_async_psycopg_url(worker_dsn))
        try:
            session_factory = make_session_factory(engine)
            async with session_factory() as session, session.begin():
                repo = SqlAlchemyJobQueueRepository(session)
                first = await repo.enqueue_if_absent(
                    job_id=job_id_a,
                    job_type="AUDIT_INTEGRITY_CHECK",
                    payload={},
                    scheduled_for=datetime.now(UTC),
                    max_attempts=3,
                    created_at=datetime.now(UTC),
                )
            async with session_factory() as session, session.begin():
                repo = SqlAlchemyJobQueueRepository(session)
                second = await repo.enqueue_if_absent(
                    job_id=job_id_b,
                    job_type="AUDIT_INTEGRITY_CHECK",
                    payload={},
                    scheduled_for=datetime.now(UTC),
                    max_attempts=3,
                    created_at=datetime.now(UTC),
                )
            return first, second
        finally:
            await engine.dispose()

    try:
        first_inserted, second_inserted = asyncio.run(run())
        assert first_inserted is True
        assert second_inserted is False

        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM core.job_queue WHERE job_type = 'AUDIT_INTEGRITY_CHECK' "
                "AND status IN ('PENDING', 'RUNNING')"
            )
            live_count = cur.fetchone()[0]
        owner_connection.rollback()
        assert live_count == 1
    finally:
        _delete_job(owner_connection, job_id_a)
        _delete_job(owner_connection, job_id_b)


# --- narrow session projection under atp_worker grants (ADR-013 Context) ---


@pytest.fixture
def seeded_expired_session(
    migrated_database: str, owner_connection: psycopg.Connection
) -> Iterator[tuple[str, str]]:
    """A user and one expired, unrevoked session row - just enough for
    `SESSION_REAP`'s query to have something real to find. `user_id` is
    returned alongside so the caller can drive `delete_user_cascade`."""
    user_id = _new_uuid()
    session_id_hash = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.users (user_id, username, password_hash, role, created_at, updated_at)
            VALUES (%s, %s, 'argon2id$fixture', 'administrator', now(), now())
            """,
            (user_id, f"fixture-{user_id}"),
        )
        cur.execute(
            """
            INSERT INTO core.sessions
                (session_id_hash, user_id, csrf_token, created_at, expires_at, revoked_at)
            VALUES (%s, %s, 'fixture-csrf', now() - interval '2 hours',
                    now() - interval '1 hour', NULL)
            """,
            (session_id_hash, user_id),
        )
    owner_connection.commit()
    yield user_id, session_id_hash
    delete_user_cascade(owner_connection, user_id)


def test_narrow_session_projection_succeeds_where_select_sessionrow_fails(
    worker_dsn: str,
    seeded_expired_session: tuple[str, str],
) -> None:
    """The exact trap ADR-013's Context section names: `atp_worker` holds
    column-scoped `SELECT` on exactly `(session_id_hash, expires_at,
    revoked_at)` of `core.sessions` - no unit test with a fake can prove
    this, since every fake in `tests/unit/worker/fakes.py` has no concept
    of a database grant at all."""
    _user_id, session_id_hash = seeded_expired_session

    async def run() -> tuple[int, Exception | None]:
        engine = create_async_engine(_as_async_psycopg_url(worker_dsn))
        try:
            session_factory = make_session_factory(engine)

            async with session_factory() as session:
                narrow_repo = SqlAlchemyWorkerSessionObservationRepository(session)
                observations = await narrow_repo.list_expired_unrevoked(now=datetime.now(UTC))
            matching = [o for o in observations if o.session_id_hash == session_id_hash]

            captured: Exception | None = None
            async with session_factory() as session:
                wide_repo = SqlAlchemySessionRepository(session)
                try:
                    await wide_repo.get_by_hash(session_id_hash)
                except sqlalchemy.exc.DBAPIError as exc:
                    captured = exc

            return len(matching), captured
        finally:
            await engine.dispose()

    matching_count, captured_error = asyncio.run(run())

    assert matching_count == 1
    assert captured_error is not None
    assert isinstance(captured_error.orig, psycopg.errors.InsufficientPrivilege)


# --- complete job lifecycle (ADR-013 Sections 2-3) --------------------------


def test_full_audit_integrity_check_lifecycle_via_run_once(
    migrated_database: str,
    owner_dsn: str,
    worker_dsn: str,
    owner_connection: psycopg.Connection,
) -> None:
    """The richest of the three job types end to end: claim (Tx A,
    `attempts` incremented), handler + audit write + terminal update
    (Tx B, one transaction - safety invariant #14), through the real
    `atp_worker` role and the production `HANDLER_REGISTRY` (no
    test-supplied registry). `AUDIT_INTEGRITY_CHECK` is chosen over
    `SESSION_REAP`/`RETENTION` here specifically because it is the only
    one of the three that writes to `audit.audit_events`, so this is also
    the one test in this file proving that write survives under the real
    `SELECT, INSERT`-only grant on that table."""
    now = datetime.now(UTC)
    window_end = now - timedelta(minutes=15)
    window_start = window_end - timedelta(minutes=15)
    job_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.job_queue
                (job_id, job_type, payload, status, attempts, max_attempts,
                 scheduled_for, created_at)
            VALUES (%s, 'AUDIT_INTEGRITY_CHECK', %s::jsonb, 'PENDING', 0, 3, now(), now())
            """,
            (
                job_id,
                f'{{"window_start": "{window_start.isoformat()}", '
                f'"window_end": "{window_end.isoformat()}"}}',
            ),
        )
    owner_connection.commit()

    async def run() -> bool:
        engine = create_async_engine(_as_async_psycopg_url(worker_dsn))
        try:
            uow_factory = worker_unit_of_work_factory(make_session_factory(engine))
            return await run_once(
                uow_factory,
                clock=UTCClock(),
                id_generator=UUIDv7Generator(),
                instance_id="worker-lifecycle",
            )
        finally:
            await engine.dispose()

    try:
        claimed_something = asyncio.run(run())
        assert claimed_something is True

        row = _fetch_job(owner_connection, job_id)
        assert row is not None
        status, attempts, locked_at, locked_by, _scheduled_for, completed_at = row
        assert status == "SUCCEEDED"
        assert attempts == 1
        assert locked_at is None
        assert locked_by is None
        assert completed_at is not None

        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT source_refs FROM audit.audit_events "
                "WHERE action = 'AUDIT_INTEGRITY_ATTESTED' "
                "AND source_refs->>'job_id' = %s",
                (job_id,),
            )
            audit_row = cur.fetchone()
        owner_connection.rollback()
        assert audit_row is not None
    finally:
        _delete_job(owner_connection, job_id)
