"""Unit tests for `atp_worker.handlers.retention.retention_handler`
(Phase 1 Step 12 Phase B, ADR-013 §2).

No Docker needed - `uow.jobs.delete_terminal_before`'s WHERE clause (real
SQL, tested against real PostgreSQL in
`tests/integration/db/test_repositories.py`... actually
`tests/unit/persistence/test_jobs_repository.py`) is what makes "never
touches PENDING/RUNNING" structural; these tests prove the handler asks
for the right cutoff and reads the payload correctly, not that PostgreSQL
enforces it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_worker.errors import HandlerFailedError
from atp_worker.handlers.retention import RETENTION_WINDOW_DAYS_DEFAULT, retention_handler
from tests.unit.worker.fakes import (
    FakeAuditEventRepository,
    FakeAuditEventWriter,
    FakeHandlerUnitOfWork,
    FakeJobQueueRepository,
    FakeSessionObservationRepository,
    claimed_job,
)

_NOW = datetime(2026, 1, 15, tzinfo=UTC)


def _uow(*, delete_result: int = 0) -> tuple[FakeHandlerUnitOfWork, FakeJobQueueRepository]:
    jobs = FakeJobQueueRepository(delete_terminal_before_result=delete_result)
    uow = FakeHandlerUnitOfWork(
        jobs=jobs,
        audit=FakeAuditEventWriter(),
        audit_events=FakeAuditEventRepository(),
        session_observations=FakeSessionObservationRepository(),
    )
    return uow, jobs


def test_retention_uses_the_default_seven_day_cutoff_when_payload_is_empty() -> None:
    uow, jobs = _uow()
    job = claimed_job(job_type="RETENTION", payload={})

    asyncio.run(
        retention_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert RETENTION_WINDOW_DAYS_DEFAULT == 7
    assert jobs.delete_terminal_before_calls == [_NOW - timedelta(days=7)]


def test_retention_honors_a_payload_supplied_window() -> None:
    uow, jobs = _uow()
    job = claimed_job(job_type="RETENTION", payload={"retention_window_days": 14})

    asyncio.run(
        retention_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert jobs.delete_terminal_before_calls == [_NOW - timedelta(days=14)]


@pytest.mark.parametrize("bad_value", [0, -1, "seven", 3.5, True])
def test_retention_rejects_an_invalid_window_without_retry(bad_value: object) -> None:
    """A malformed payload cannot fix itself by retrying - the same
    reasoning applied to a malformed AUDIT_INTEGRITY_CHECK window."""
    uow, jobs = _uow()
    job = claimed_job(job_type="RETENTION", payload={"retention_window_days": bad_value})

    with pytest.raises(HandlerFailedError) as exc_info:
        asyncio.run(
            retention_handler(
                uow,
                job,
                clock=FrozenClock(_NOW),
                id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.retryable is False
    assert jobs.delete_terminal_before_calls == []


def test_retention_writes_no_audit_event() -> None:
    """ADR-013 Section 2: job-table housekeeping is operational state, not
    something the immutable audit ledger should carry."""
    uow, _jobs = _uow()
    job = claimed_job(job_type="RETENTION")

    asyncio.run(
        retention_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert uow.audit.saved == []


def test_running_twice_with_nothing_new_to_delete_is_safe() -> None:
    """ADR-013 Section 7: RETENTION is genuinely idempotent - a second run
    against an already-pruned state deletes zero additional rows. The
    fake's delete_terminal_before_result stands in for "the database
    already deleted everything eligible" - this asserts the handler does
    not error or behave differently on that second call."""
    uow, jobs = _uow(delete_result=0)
    job = claimed_job(job_type="RETENTION")
    clock = FrozenClock(_NOW)

    asyncio.run(retention_handler(uow, job, clock=clock, id_generator=SequentialIdGenerator()))  # type: ignore[arg-type]
    asyncio.run(retention_handler(uow, job, clock=clock, id_generator=SequentialIdGenerator()))  # type: ignore[arg-type]

    assert len(jobs.delete_terminal_before_calls) == 2
    assert jobs.delete_terminal_before_calls[0] == jobs.delete_terminal_before_calls[1]
