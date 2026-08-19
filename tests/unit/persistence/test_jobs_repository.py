"""Unit tests for `atp_persistence.repositories.jobs.SqlAlchemyJobQueueRepository`
(Phase 1 Step 12 Phase B, ADR-013).

No Docker needed - these prove the *statement construction* and *field
mutation* logic this class owns (the WHERE/ORDER BY/FOR UPDATE SKIP LOCKED
shape of the claim query, which fields each mark_* method touches, the
IntegrityError-to-bool translation in `enqueue_if_absent`) via
`tests.unit.persistence.fakes.FakeAsyncSession`. What a fake cannot prove -
that PostgreSQL actually honors `FOR UPDATE SKIP LOCKED` under concurrent
claimants, that the real `atp_worker` role can execute this SQL, that the
partial unique index really produces the `IntegrityError` this module
expects - is Docker-gated integration coverage, deliberately out of scope
here (Phase 1 Step 12 Phase B's repository-layer pass; the worker
integration suite is a later step).

Follows `tests/unit/exec_paper/`'s convention: no pytest-asyncio,
`asyncio.run()` inside plain sync test functions.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from atp_persistence.models.core import JobQueueRow
from atp_persistence.repositories.jobs import ClaimedJob, ExpiredLease, SqlAlchemyJobQueueRepository
from tests.unit.persistence.fakes import FakeAsyncSession, FakeResult

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _job_row(
    *,
    job_id: str = "job-1",
    job_type: str = "RETENTION",
    status: str = "PENDING",
    attempts: int = 0,
    max_attempts: int = 3,
    scheduled_for: datetime = _NOW,
    locked_at: datetime | None = None,
    locked_by: str | None = None,
    completed_at: datetime | None = None,
    last_error: str | None = None,
    payload: dict[str, object] | None = None,
) -> JobQueueRow:
    return JobQueueRow(
        job_id=job_id,
        job_type=job_type,
        payload=payload if payload is not None else {},
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
        scheduled_for=scheduled_for,
        locked_at=locked_at,
        locked_by=locked_by,
        completed_at=completed_at,
        last_error=last_error,
        created_at=_NOW,
    )


def _compiled(statement: object) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


# --- claim_next --------------------------------------------------------


def test_claim_next_statement_uses_for_update_skip_locked() -> None:
    """ADR-013 Tx A: concurrent claimants must never block on each other -
    `FOR UPDATE SKIP LOCKED`, not plain `FOR UPDATE`."""
    session = FakeAsyncSession()
    session.queue_result(FakeResult(scalar=None))
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.claim_next(now=_NOW, instance_id="worker-1"))

    compiled = _compiled(session.executed_statements[0])
    assert "FOR UPDATE SKIP LOCKED" in compiled


def test_claim_next_statement_filters_pending_and_due_only() -> None:
    session = FakeAsyncSession()
    session.queue_result(FakeResult(scalar=None))
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.claim_next(now=_NOW, instance_id="worker-1"))

    compiled = _compiled(session.executed_statements[0])
    assert "job_queue.status = " in compiled
    assert "job_queue.scheduled_for <= " in compiled
    assert "ORDER BY core.job_queue.scheduled_for, core.job_queue.job_id" in compiled
    assert "LIMIT" in compiled


def test_claim_next_returns_none_when_queue_is_empty() -> None:
    session = FakeAsyncSession()
    session.queue_result(FakeResult(scalar=None))
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    claimed = asyncio.run(repo.claim_next(now=_NOW, instance_id="worker-1"))

    assert claimed is None


def test_claim_next_increments_attempts_and_marks_running() -> None:
    """ADR-013 Section 3: attempts increments at claim, not at failure -
    so a crash immediately after this call still costs exactly one
    attempt."""
    row = _job_row(status="PENDING", attempts=1, locked_at=None, locked_by=None)
    session = FakeAsyncSession()
    session.queue_result(FakeResult(scalar=row))
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    claimed = asyncio.run(repo.claim_next(now=_NOW, instance_id="worker-7"))

    assert row.status == "RUNNING"
    assert row.attempts == 2
    assert row.locked_at == _NOW
    assert row.locked_by == "worker-7"
    assert claimed == ClaimedJob(
        job_id=row.job_id, job_type=row.job_type, payload=row.payload, attempts=2, max_attempts=3
    )


# --- mark_succeeded / mark_failed_retryable / mark_failed_terminal -----


def test_mark_succeeded_sets_terminal_state_and_clears_lease() -> None:
    row = _job_row(status="RUNNING", locked_at=_NOW, locked_by="worker-1")
    session = FakeAsyncSession()
    session.seed_get(row.job_id, row)
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.mark_succeeded(row.job_id, completed_at=_NOW))

    assert row.status == "SUCCEEDED"
    assert row.completed_at == _NOW
    assert row.locked_at is None
    assert row.locked_by is None


def test_mark_succeeded_is_a_no_op_for_an_unknown_job_id() -> None:
    """Mirrors SqlAlchemySessionRepository.revoke's existing idempotent
    no-op precedent for a row that no longer exists."""
    session = FakeAsyncSession()
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.mark_succeeded("does-not-exist", completed_at=_NOW))  # must not raise


def test_mark_failed_retryable_returns_to_pending_without_completed_at() -> None:
    """ADR-013 Section 3: a retryable failure is never terminal -
    completed_at must stay untouched (ck_job_queue_terminal_state_has_completed_at)."""
    retry_at = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    row = _job_row(status="RUNNING", attempts=1, locked_at=_NOW, locked_by="worker-1")
    session = FakeAsyncSession()
    session.seed_get(row.job_id, row)
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.mark_failed_retryable(row.job_id, scheduled_for=retry_at))

    assert row.status == "PENDING"
    assert row.scheduled_for == retry_at
    assert row.completed_at is None
    assert row.locked_at is None
    assert row.locked_by is None


def test_mark_failed_terminal_sets_failed_with_completed_at_and_last_error() -> None:
    row = _job_row(
        status="RUNNING", attempts=3, max_attempts=3, locked_at=_NOW, locked_by="worker-1"
    )
    session = FakeAsyncSession()
    session.seed_get(row.job_id, row)
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    asyncio.run(
        repo.mark_failed_terminal(row.job_id, completed_at=_NOW, last_error="ValueError: boom")
    )

    assert row.status == "FAILED"
    assert row.completed_at == _NOW
    assert row.last_error == "ValueError: boom"
    assert row.locked_at is None
    assert row.locked_by is None


# --- reclaim_expired_leases ---------------------------------------------


def test_reclaim_expired_leases_statement_uses_skip_locked_and_filters_running() -> None:
    session = FakeAsyncSession()
    session.queue_result(FakeResult(scalars_list=[]))
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.reclaim_expired_leases(older_than=_NOW))

    compiled = _compiled(session.executed_statements[0])
    assert "FOR UPDATE SKIP LOCKED" in compiled
    assert "job_queue.status = " in compiled
    assert "job_queue.locked_at < " in compiled


def test_reclaim_expired_leases_maps_rows_to_expired_lease() -> None:
    row_a = _job_row(job_id="a", job_type="RETENTION", status="RUNNING", attempts=1, max_attempts=3)
    row_b = _job_row(
        job_id="b", job_type="SESSION_REAP", status="RUNNING", attempts=3, max_attempts=3
    )
    session = FakeAsyncSession()
    session.queue_result(FakeResult(scalars_list=[row_a, row_b]))
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    leases = asyncio.run(repo.reclaim_expired_leases(older_than=_NOW))

    assert list(leases) == [
        ExpiredLease(job_id="a", job_type="RETENTION", attempts=1, max_attempts=3),
        ExpiredLease(job_id="b", job_type="SESSION_REAP", attempts=3, max_attempts=3),
    ]


# --- enqueue_if_absent ---------------------------------------------------


def test_enqueue_if_absent_returns_true_on_a_clean_insert() -> None:
    session = FakeAsyncSession()
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    inserted = asyncio.run(
        repo.enqueue_if_absent(
            job_id="job-new",
            job_type="RETENTION",
            payload={},
            scheduled_for=_NOW,
            max_attempts=3,
            created_at=_NOW,
        )
    )

    assert inserted is True
    assert len(session.added) == 1


def test_enqueue_if_absent_swallows_integrity_error_and_returns_false() -> None:
    """ADR-013 Section 6: the partial unique index
    ux_job_queue_one_live_per_type is the actual enforcement - this
    method's job is only to translate that collision into a bool, the
    same way every other insert-then-catch call site in this codebase
    handles its own UNIQUE violation, just with the catch happening
    inside this method rather than in a caller."""
    session = FakeAsyncSession()
    session.set_flush_raises(
        IntegrityError("INSERT INTO core.job_queue ...", {}, Exception("duplicate key value"))
    )
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    inserted = asyncio.run(
        repo.enqueue_if_absent(
            job_id="job-dup",
            job_type="RETENTION",
            payload={},
            scheduled_for=_NOW,
            max_attempts=3,
            created_at=_NOW,
        )
    )

    assert inserted is False


# --- delete_terminal_before ---------------------------------------------


def test_delete_terminal_before_statement_only_targets_terminal_statuses() -> None:
    session = FakeAsyncSession()
    session.queue_result(FakeResult(rowcount=0))
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.delete_terminal_before(cutoff=_NOW))

    compiled = _compiled(session.executed_statements[0])
    assert "DELETE FROM core.job_queue" in compiled
    assert "job_queue.status IN" in compiled
    assert "job_queue.completed_at < " in compiled


def test_delete_terminal_before_returns_the_deleted_row_count() -> None:
    session = FakeAsyncSession()
    session.queue_result(FakeResult(rowcount=5))
    repo = SqlAlchemyJobQueueRepository(session)  # type: ignore[arg-type]

    deleted = asyncio.run(repo.delete_terminal_before(cutoff=_NOW))

    assert deleted == 5
