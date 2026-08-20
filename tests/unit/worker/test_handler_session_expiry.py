"""Unit tests for `atp_worker.handlers.session_expiry.session_reap_handler`
(Phase 1 Step 12 Phase B, ADR-013 §2).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_persistence.repositories.session_observations import SessionExpiryObservation
from atp_platform.metrics import PLATFORM_REGISTRY
from atp_worker.handlers.session_expiry import session_reap_handler
from tests.unit.worker.fakes import (
    FakeAuditEventRepository,
    FakeAuditEventWriter,
    FakeHandlerUnitOfWork,
    FakeJobQueueRepository,
    FakeSessionObservationRepository,
    claimed_job,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _uow(
    *, observations: list[SessionExpiryObservation] | None = None
) -> tuple[FakeHandlerUnitOfWork, FakeSessionObservationRepository]:
    session_observations = FakeSessionObservationRepository(observations=observations)
    uow = FakeHandlerUnitOfWork(
        jobs=FakeJobQueueRepository(),
        audit=FakeAuditEventWriter(),
        audit_events=FakeAuditEventRepository(),
        session_observations=session_observations,
    )
    return uow, session_observations


def test_session_reap_calls_the_narrow_projection_with_the_injected_clock() -> None:
    uow, session_observations = _uow()
    job = claimed_job(job_type="SESSION_REAP")

    asyncio.run(
        session_reap_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert session_observations.calls == [_NOW]


def test_session_reap_is_observation_only_no_audit_event_written() -> None:
    """ADR-013 Section 2: an observation is not a state change."""
    uow, _sessions = _uow(observations=[SessionExpiryObservation("hash-a", _NOW, None)])
    job = claimed_job(job_type="SESSION_REAP")

    asyncio.run(
        session_reap_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert uow.audit.saved == []


def test_session_reap_never_touches_jobs_or_writes_anything() -> None:
    """The only repository this handler calls at all is
    session_observations - confirmed by the other three fakes staying
    completely untouched."""
    uow, _sessions = _uow(observations=[SessionExpiryObservation("hash-a", _NOW, None)])
    job = claimed_job(job_type="SESSION_REAP")

    asyncio.run(
        session_reap_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert uow.jobs.claim_calls == []
    assert uow.jobs.succeeded == []
    assert uow.jobs.failed_retryable == []
    assert uow.jobs.failed_terminal == []
    assert uow.jobs.delete_terminal_before_calls == []
    assert uow.audit_events.list_recent_calls == []
    assert uow.audit_events.window_stats_calls == []


def test_session_reap_repository_exposes_no_mutation_method_at_all() -> None:
    """Structural guard: SqlAlchemyWorkerSessionObservationRepository (and
    its fake here) has no save/revoke/delete method for this handler to
    even accidentally call - the absence itself is the safety property."""
    for forbidden in ("save", "revoke", "delete", "update"):
        assert not hasattr(FakeSessionObservationRepository, forbidden)


def test_session_reap_handles_zero_expired_sessions() -> None:
    uow, _sessions = _uow(observations=[])
    job = claimed_job(job_type="SESSION_REAP")

    asyncio.run(
        session_reap_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )  # must not raise


def test_session_reap_sets_the_expired_unrevoked_gauge_to_the_observed_count() -> None:
    """A `Gauge`, not a `Counter` (ADR-013 Section 2, Phase 1 Step 13):
    `.set()` overwrites, so reading it immediately after this call is safe
    against the value any earlier test in the same process left behind -
    no delta/before-after comparison needed, unlike a Counter under a
    shared registry."""
    uow, _sessions = _uow(
        observations=[
            SessionExpiryObservation("hash-a", _NOW, None),
            SessionExpiryObservation("hash-b", _NOW, None),
        ]
    )
    job = claimed_job(job_type="SESSION_REAP")

    asyncio.run(
        session_reap_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    value = PLATFORM_REGISTRY.get_sample_value("atp_worker_session_reap_expired_unrevoked_count")
    assert value == 2.0


def test_session_reap_sets_the_gauge_to_zero_not_leaving_a_stale_value() -> None:
    uow, _sessions = _uow(observations=[])
    job = claimed_job(job_type="SESSION_REAP")

    asyncio.run(
        session_reap_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    value = PLATFORM_REGISTRY.get_sample_value("atp_worker_session_reap_expired_unrevoked_count")
    assert value == 0.0
