"""Unit tests for
`atp_worker.handlers.audit_integrity.audit_integrity_check_handler`
(Phase 1 Step 12 Phase B, ADR-013 §2, §9).

Proves the handler's own logic - payload parsing, which SQL aggregate it
asks for, what it writes and when, that a mismatch is SUCCEEDED plus a
violation event rather than a raised exception. The aggregate query itself
(`window_attestation_stats`) is real SQL, tested against real PostgreSQL
separately; these tests fake its result.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from atp_domain.audit import (
    ACTION_AUDIT_INTEGRITY_ATTESTED,
    ACTION_AUDIT_INTEGRITY_VIOLATION_DETECTED,
    AuditEvent,
)
from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_domain.types import ActorType, EventId
from atp_persistence.repositories.audit_events import WindowAttestationStats
from atp_worker.errors import HandlerFailedError
from atp_worker.handlers.audit_integrity import audit_integrity_check_handler
from tests.unit.worker.fakes import (
    FakeAuditEventRepository,
    FakeAuditEventWriter,
    FakeHandlerUnitOfWork,
    FakeJobQueueRepository,
    FakeSessionObservationRepository,
    claimed_job,
)

_WINDOW_START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
_WINDOW_END = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
_NOW = _WINDOW_END + timedelta(minutes=1)  # a closed past window relative to now

_PAYLOAD = {
    "window_start": _WINDOW_START.isoformat(),
    "window_end": _WINDOW_END.isoformat(),
}


def _uow(
    *,
    stats: WindowAttestationStats,
    prior_attestations: list[AuditEvent] | None = None,
) -> tuple[FakeHandlerUnitOfWork, FakeAuditEventWriter, FakeAuditEventRepository]:
    audit = FakeAuditEventWriter()
    audit_events = FakeAuditEventRepository(
        recent=prior_attestations,
        stats_by_window={(_WINDOW_START.isoformat(), _WINDOW_END.isoformat()): stats},
    )
    uow = FakeHandlerUnitOfWork(
        jobs=FakeJobQueueRepository(),
        audit=audit,
        audit_events=audit_events,
        session_observations=FakeSessionObservationRepository(),
    )
    return uow, audit, audit_events


def _prior_attestation(
    *, observed_count: str, max_event_id: str, max_recorded_at: str
) -> AuditEvent:
    return AuditEvent(
        event_id=EventId("00000000-0000-7000-8000-000000000001"),
        correlation_id="corr-prior",
        occurred_at=_WINDOW_END,
        recorded_at=_WINDOW_END,
        actor_type=ActorType.SYSTEM,
        actor_id="atp_worker/job-prior",
        action=ACTION_AUDIT_INTEGRITY_ATTESTED,
        mode=None,
        strategy_id=None,
        strategy_version=None,
        instrument_id=None,
        source_refs={
            "job_id": "job-prior",
            "job_type": "AUDIT_INTEGRITY_CHECK",
            "window_start": _WINDOW_START.isoformat(),
            "window_end": _WINDOW_END.isoformat(),
            "observed_count": observed_count,
            "max_event_id": max_event_id,
            "max_recorded_at": max_recorded_at,
        },
    )


# --- first run: no prior attestation ------------------------------------


def test_first_run_writes_exactly_one_attestation_and_no_violation() -> None:
    stats = WindowAttestationStats(
        observed_count=3, max_event_id="event-max-1", max_recorded_at=_WINDOW_END
    )
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert len(audit.saved) == 1
    attestation = audit.saved[0]
    assert attestation.action == ACTION_AUDIT_INTEGRITY_ATTESTED


def test_attestation_source_refs_contain_the_computed_stats_as_strings() -> None:
    stats = WindowAttestationStats(
        observed_count=3, max_event_id="event-max-1", max_recorded_at=_WINDOW_END
    )
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(job_id="job-x", job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    refs = audit.saved[0].source_refs
    assert refs["job_id"] == "job-x"
    assert refs["job_type"] == "AUDIT_INTEGRITY_CHECK"
    assert refs["window_start"] == _WINDOW_START.isoformat()
    assert refs["window_end"] == _WINDOW_END.isoformat()
    assert refs["observed_count"] == "3"
    assert refs["max_event_id"] == "event-max-1"
    assert refs["max_recorded_at"] == _WINDOW_END.isoformat()
    for value in refs.values():
        assert isinstance(value, str)


def test_attestation_actor_and_mode_match_adr_013() -> None:
    stats = WindowAttestationStats(observed_count=0, max_event_id=None, max_recorded_at=None)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(job_id="job-y", job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    event = audit.saved[0]
    assert event.actor_type is ActorType.SYSTEM
    assert event.actor_id == "atp_worker/job-y"
    assert event.mode is None


def test_empty_window_produces_zero_count_and_none_maxima() -> None:
    stats = WindowAttestationStats(observed_count=0, max_event_id=None, max_recorded_at=None)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    refs = audit.saved[0].source_refs
    assert refs["observed_count"] == "0"
    assert refs["max_event_id"] == ""
    assert refs["max_recorded_at"] == ""


def test_deterministic_id_generator_produces_the_expected_event_id() -> None:
    stats = WindowAttestationStats(observed_count=1, max_event_id="e1", max_recorded_at=_WINDOW_END)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert audit.saved[0].event_id == "00000000-0000-7000-8000-000000000001"


# --- second run: matching prior attestation ------------------------------


def test_second_run_matching_prior_writes_a_new_attestation_but_no_violation() -> None:
    """ADR-013 Section 7: value-idempotent, not row-idempotent - a second
    run over the same window always writes a second attestation row
    (the ledger is append-only and cannot do otherwise), but no violation
    event when the two agree."""
    prior = _prior_attestation(
        observed_count="3", max_event_id="event-max-1", max_recorded_at=_WINDOW_END.isoformat()
    )
    stats = WindowAttestationStats(
        observed_count=3, max_event_id="event-max-1", max_recorded_at=_WINDOW_END
    )
    uow, audit, events = _uow(stats=stats, prior_attestations=[prior])
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert len(audit.saved) == 1
    assert audit.saved[0].action == ACTION_AUDIT_INTEGRITY_ATTESTED
    assert events.list_recent_calls == [(ACTION_AUDIT_INTEGRITY_ATTESTED, 200)]


# --- second run: mismatched prior -> violation, still SUCCEEDED ---------


def test_mismatched_prior_writes_attestation_plus_violation_and_does_not_raise() -> None:
    """ADR-013 Section 2: a detected violation is a successful check, not
    a failed job - the handler must return normally."""
    prior = _prior_attestation(
        observed_count="3", max_event_id="event-max-1", max_recorded_at=_WINDOW_END.isoformat()
    )
    stats = WindowAttestationStats(  # a row vanished - count dropped
        observed_count=2, max_event_id="event-max-0", max_recorded_at=_WINDOW_END
    )
    uow, audit, _events = _uow(stats=stats, prior_attestations=[prior])
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )  # must not raise

    actions = [event.action for event in audit.saved]
    assert actions == [ACTION_AUDIT_INTEGRITY_ATTESTED, ACTION_AUDIT_INTEGRITY_VIOLATION_DETECTED]


def test_violation_source_refs_carry_both_attested_and_current_values() -> None:
    prior = _prior_attestation(
        observed_count="3", max_event_id="event-max-1", max_recorded_at=_WINDOW_END.isoformat()
    )
    stats = WindowAttestationStats(
        observed_count=2, max_event_id="event-max-0", max_recorded_at=_WINDOW_END
    )
    uow, audit, _events = _uow(stats=stats, prior_attestations=[prior])
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    violation = audit.saved[1]
    refs = violation.source_refs
    assert refs["attested_observed_count"] == "3"
    assert refs["current_observed_count"] == "2"
    assert refs["attested_max_event_id"] == "event-max-1"
    assert refs["current_max_event_id"] == "event-max-0"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: WindowAttestationStats(4, s.max_event_id, s.max_recorded_at),  # count differs
        lambda s: WindowAttestationStats(s.observed_count, "different-max", s.max_recorded_at),
        # max_recorded_at alone differs - count and max_event_id both
        # unchanged. This is the case ADR-013 §2's "backdating into an
        # already-attested window" claim rests on, and the only one of the
        # three attested values that can move while the other two hold
        # still (a delete-plus-backdated-insert preserving both count and
        # the maximum event_id). It is why §9's key list carries
        # max_recorded_at as a seventh key: drop the key and this
        # detection is silently lost while every other test still passes.
        lambda s: WindowAttestationStats(
            s.observed_count, s.max_event_id, _WINDOW_END + timedelta(hours=6)
        ),
    ],
)
def test_any_single_field_mismatch_is_detected(mutate: object) -> None:
    prior = _prior_attestation(
        observed_count="3", max_event_id="event-max-1", max_recorded_at=_WINDOW_END.isoformat()
    )
    baseline = WindowAttestationStats(
        observed_count=3, max_event_id="event-max-1", max_recorded_at=_WINDOW_END
    )
    stats = mutate(baseline)  # type: ignore[operator]
    uow, audit, _events = _uow(stats=stats, prior_attestations=[prior])
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))

    asyncio.run(
        audit_integrity_check_handler(
            uow,
            job,
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
        )
    )

    assert ACTION_AUDIT_INTEGRITY_VIOLATION_DETECTED in [e.action for e in audit.saved]


# --- payload validation ---------------------------------------------------


def test_missing_window_start_fails_without_retry() -> None:
    stats = WindowAttestationStats(observed_count=0, max_event_id=None, max_recorded_at=None)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(
        job_type="AUDIT_INTEGRITY_CHECK", payload={"window_end": _WINDOW_END.isoformat()}
    )

    with pytest.raises(HandlerFailedError) as exc_info:
        asyncio.run(
            audit_integrity_check_handler(
                uow,
                job,
                clock=FrozenClock(_NOW),
                id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.retryable is False
    assert audit.saved == []


def test_non_string_window_bound_fails_without_retry() -> None:
    stats = WindowAttestationStats(observed_count=0, max_event_id=None, max_recorded_at=None)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(
        job_type="AUDIT_INTEGRITY_CHECK",
        payload={"window_start": 12345, "window_end": _WINDOW_END.isoformat()},
    )

    with pytest.raises(HandlerFailedError) as exc_info:
        asyncio.run(
            audit_integrity_check_handler(
                uow,
                job,
                clock=FrozenClock(_NOW),
                id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.retryable is False


def test_unparsable_window_bound_fails_without_retry() -> None:
    stats = WindowAttestationStats(observed_count=0, max_event_id=None, max_recorded_at=None)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(
        job_type="AUDIT_INTEGRITY_CHECK",
        payload={"window_start": "not-a-date", "window_end": _WINDOW_END.isoformat()},
    )

    with pytest.raises(HandlerFailedError) as exc_info:
        asyncio.run(
            audit_integrity_check_handler(
                uow,
                job,
                clock=FrozenClock(_NOW),
                id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.retryable is False


def test_timezone_naive_window_bound_fails_without_retry() -> None:
    stats = WindowAttestationStats(observed_count=0, max_event_id=None, max_recorded_at=None)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(
        job_type="AUDIT_INTEGRITY_CHECK",
        payload={
            "window_start": "2026-01-01T00:00:00",  # no tzinfo
            "window_end": _WINDOW_END.isoformat(),
        },
    )

    with pytest.raises(HandlerFailedError) as exc_info:
        asyncio.run(
            audit_integrity_check_handler(
                uow,
                job,
                clock=FrozenClock(_NOW),
                id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.retryable is False


def test_window_end_not_after_window_start_fails_without_retry() -> None:
    stats = WindowAttestationStats(observed_count=0, max_event_id=None, max_recorded_at=None)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(
        job_type="AUDIT_INTEGRITY_CHECK",
        payload={
            "window_start": _WINDOW_END.isoformat(),
            "window_end": _WINDOW_START.isoformat(),
        },
    )

    with pytest.raises(HandlerFailedError) as exc_info:
        asyncio.run(
            audit_integrity_check_handler(
                uow,
                job,
                clock=FrozenClock(_NOW),
                id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.retryable is False


def test_future_facing_window_fails_without_retry() -> None:
    """ADR-013 Section 2: 'a closed past window only.'"""
    stats = WindowAttestationStats(observed_count=0, max_event_id=None, max_recorded_at=None)
    uow, audit, _events = _uow(stats=stats, prior_attestations=[])
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK", payload=dict(_PAYLOAD))
    # `_NOW` here predates the window's own end, so it is still "open".
    still_open_clock = FrozenClock(_WINDOW_START)

    with pytest.raises(HandlerFailedError) as exc_info:
        asyncio.run(
            audit_integrity_check_handler(
                uow,
                job,
                clock=still_open_clock,
                id_generator=SequentialIdGenerator(),  # type: ignore[arg-type]
            )
        )

    assert exc_info.value.retryable is False
    assert audit.saved == []
