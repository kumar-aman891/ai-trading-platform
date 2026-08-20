"""Unit tests for `atp_worker.runner` (Phase 1 Step 12 Phase B, ADR-013).

These prove the *transaction choreography* `runner` owns: that the claim
transaction closes before a handler runs, that failure bookkeeping opens a
transaction of its own after the handler's has rolled back, that backoff
and exhaustion route to the states ADR-013 §3-§5 specify, and that the
lease sweep runs first in a poll cycle.

They deliberately prove nothing about PostgreSQL. `FOR UPDATE SKIP LOCKED`
exclusivity under real concurrent claimants, the real `atp_worker` role's
ability to run this SQL, and the partial unique index's behavior are all
database properties that need a real database - Docker-gated worker
integration coverage, a later step. A passing suite here means `runner`
asks for the right things in the right order, not that the database
answers correctly.

Follows `tests/unit/exec_paper/`'s convention: no pytest-asyncio,
`asyncio.run()` inside plain sync test functions, `FrozenClock` built at
the call site.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_persistence.repositories.jobs import ExpiredLease
from atp_platform.metrics import PLATFORM_REGISTRY
from atp_worker.errors import HandlerFailedError, NoHandlerRegisteredError
from atp_worker.runner import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_CAP_SECONDS,
    LEASE_DURATION_SECONDS,
    MAX_LAST_ERROR_LENGTH,
    backoff_delay,
    format_last_error,
    run_once,
    run_poll_cycle,
    run_poll_loop,
    sweep_expired_leases,
)
from tests.unit.worker.fakes import (
    FakeJobQueueRepository,
    RecordingHandler,
    RecordingUnitOfWorkFactory,
    claimed_job,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_INSTANCE = "worker-test-1"


def _wire(
    *,
    claimable: list[object] | None = None,
    expired_leases: list[ExpiredLease] | None = None,
    handler: RecordingHandler | None = None,
) -> tuple[RecordingUnitOfWorkFactory, FakeJobQueueRepository, RecordingHandler]:
    jobs = FakeJobQueueRepository(
        claimable=list(claimable or []),  # type: ignore[arg-type]
        expired_leases=list(expired_leases or []),
    )
    factory = RecordingUnitOfWorkFactory(jobs=jobs)
    active_handler = handler if handler is not None else RecordingHandler()
    active_handler.factory = factory
    return factory, jobs, active_handler


# --- backoff (ADR-013 Section 4) ----------------------------------------


@pytest.mark.parametrize(
    ("attempts", "expected_seconds"),
    [(1, 5), (2, 10), (3, 20), (4, 40), (5, 80), (6, 160), (7, 300), (99, 300)],
)
def test_backoff_follows_the_adr_curve_and_respects_the_cap(
    attempts: int, expected_seconds: int
) -> None:
    """backoff(attempts) = min(5 * 2 ** (attempts - 1), 300)."""
    assert backoff_delay(attempts) == timedelta(seconds=expected_seconds)


def test_backoff_constants_match_the_adr() -> None:
    assert BACKOFF_BASE_SECONDS == 5
    assert BACKOFF_CAP_SECONDS == 300
    assert LEASE_DURATION_SECONDS == 300


# --- last_error formatting (ADR-013 Section 8) --------------------------


def test_format_last_error_is_class_name_plus_message_never_a_traceback() -> None:
    formatted = format_last_error(ValueError("something broke"))

    assert formatted == "ValueError: something broke"
    assert "Traceback" not in formatted


def test_format_last_error_truncates_to_the_adr_limit() -> None:
    """A message of ordinary short words survives redaction untouched
    (nothing in it is secret-shaped), so this isolates truncation."""
    formatted = format_last_error(ValueError("boom " * 1000))

    assert len(formatted) == MAX_LAST_ERROR_LENGTH


def test_format_last_error_redacts_secret_shaped_content() -> None:
    """ADR-013 Section 8 routes last_error through the same redaction
    pipeline as logs, because an operator reads this column.

    `redact_text` matches on *shape* - JWTs, and hex/base64 runs of 32+
    characters - not on surrounding key names, which is `redact_mapping`'s
    job and needs a key/value structure free text does not have. A
    32-hex-character token is exactly what it does catch. Its known
    limits are deliberate and documented in `atp_platform.redaction`:
    a hyphenated secret (`api_key=super-secret-value-12345`) passes
    through, and so does a UUID - the latter on purpose, since redacting
    every UUID would destroy the IDs that make a `last_error` useful.
    This test asserts the capability that exists, not one that does not."""
    formatted = format_last_error(
        ValueError("upstream rejected token deadbeefcafebabe0123456789abcdef0123456789abcdef")
    )

    assert "deadbeefcafebabe0123456789abcdef" not in formatted
    assert "REDACTED" in formatted


# --- Tx A / Tx B ordering -----------------------------------------------


def test_handler_never_runs_while_the_claim_transaction_is_open() -> None:
    """ADR-013 Section 3: the claim commits *before* the handler runs, so
    a slow handler never holds the claimed row's lock. Proven two ways -
    by the recorded event ordering, and by the transaction depth observed
    from inside the handler itself."""
    job = claimed_job()
    factory, _jobs, handler = _wire(claimable=[job])

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert factory.events == [
        "tx_open",  # Tx A
        "tx_commit",
        "tx_close",
        "tx_open",  # Tx B
        "handler",
        "tx_commit",
        "tx_close",
    ]
    # Exactly one transaction open when the handler ran - its own (Tx B).
    assert handler.open_transactions_when_called == [1]


def test_successful_job_is_marked_succeeded_inside_the_handler_transaction() -> None:
    """ADR-013 Section 3 (safety invariant #14): the handler's work and
    the terminal state update commit together - so mark_succeeded must
    land before Tx B's commit, not in a transaction of its own."""
    job = claimed_job()
    factory, jobs, handler = _wire(claimable=[job])

    claimed = asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert claimed is True
    assert factory.transactions_opened == 2  # Tx A + Tx B only; no Tx C
    assert len(jobs.succeeded) == 1
    assert jobs.succeeded[0].job_id == job.job_id
    assert jobs.succeeded[0].completed_at == _NOW
    assert jobs.failed_retryable == []
    assert jobs.failed_terminal == []


def test_claim_uses_the_injected_clock_and_instance_id() -> None:
    """ADR-013 Section 12: every 'now' comes from the injected Clock."""
    factory, jobs, handler = _wire(claimable=[])

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={},
        )
    )

    assert jobs.claim_calls == [(_NOW, _INSTANCE)]


def test_run_once_returns_false_and_opens_only_the_claim_transaction_when_idle() -> None:
    factory, _jobs, _handler = _wire(claimable=[])

    claimed = asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={},
        )
    )

    assert claimed is False
    assert factory.events == ["tx_open", "tx_commit", "tx_close"]


# --- Tx C: failure bookkeeping after Tx B rolled back -------------------


def test_failure_bookkeeping_opens_a_fresh_transaction_after_the_handler_rolled_back() -> None:
    """ADR-013 Section 3: Tx B's unit of work is unusable once rolled
    back, so Tx C must be a separate transaction - the event log shows
    the rollback closing before the third transaction opens."""
    job = claimed_job(attempts=1, max_attempts=3)
    handler = RecordingHandler(raises=HandlerFailedError("handler blew up"))
    factory, jobs, handler = _wire(claimable=[job], handler=handler)

    claimed = asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert claimed is True
    assert factory.events == [
        "tx_open",  # Tx A: claim
        "tx_commit",
        "tx_close",
        "tx_open",  # Tx B: handler, which raises
        "handler",
        "tx_rollback",
        "tx_close",
        "tx_open",  # Tx C: fresh transaction for bookkeeping
        "tx_commit",
        "tx_close",
    ]
    assert len(jobs.failed_retryable) == 1


def test_retryable_failure_schedules_the_next_attempt_using_the_backoff_curve() -> None:
    """attempts == 1 after the claim increment, so the first retry waits
    BACKOFF_BASE_SECONDS (ADR-013 Section 4)."""
    job = claimed_job(attempts=1, max_attempts=3)
    handler = RecordingHandler(raises=HandlerFailedError("transient"))
    factory, jobs, handler = _wire(claimable=[job], handler=handler)

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert jobs.failed_terminal == []
    assert len(jobs.failed_retryable) == 1
    assert jobs.failed_retryable[0].job_id == job.job_id
    assert jobs.failed_retryable[0].scheduled_for == _NOW + timedelta(seconds=BACKOFF_BASE_SECONDS)


def test_second_retry_uses_the_doubled_backoff() -> None:
    job = claimed_job(attempts=2, max_attempts=3)
    handler = RecordingHandler(raises=HandlerFailedError("transient"))
    factory, jobs, handler = _wire(claimable=[job], handler=handler)

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert jobs.failed_retryable[0].scheduled_for == _NOW + timedelta(seconds=10)


def test_exhausted_retryable_failure_becomes_failed_with_completed_at() -> None:
    """attempts == max_attempts: no attempts remain, so the job is
    terminal (ADR-013 Section 3) - FAILED, with completed_at set, never
    left PENDING."""
    job = claimed_job(attempts=3, max_attempts=3)
    handler = RecordingHandler(raises=HandlerFailedError("still broken"))
    factory, jobs, handler = _wire(claimable=[job], handler=handler)

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert jobs.failed_retryable == []
    assert len(jobs.failed_terminal) == 1
    assert jobs.failed_terminal[0].job_id == job.job_id
    assert jobs.failed_terminal[0].completed_at == _NOW
    assert jobs.failed_terminal[0].last_error is not None
    assert "still broken" in jobs.failed_terminal[0].last_error


def test_non_retryable_handler_failure_is_terminal_even_with_attempts_remaining() -> None:
    job = claimed_job(attempts=1, max_attempts=3)
    handler = RecordingHandler(raises=HandlerFailedError("unfixable", retryable=False))
    factory, jobs, handler = _wire(claimable=[job], handler=handler)

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert jobs.failed_retryable == []
    assert len(jobs.failed_terminal) == 1


def test_an_unexpected_handler_exception_is_treated_as_retryable() -> None:
    """runner cannot know whether an arbitrary exception is transient, so
    it assumes it is (ADR-013 Section 3's default route)."""
    job = claimed_job(attempts=1, max_attempts=3)
    handler = RecordingHandler(raises=RuntimeError("something unexpected"))
    factory, jobs, handler = _wire(claimable=[job], handler=handler)

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert len(jobs.failed_retryable) == 1
    assert jobs.failed_terminal == []


def test_unknown_job_type_fails_immediately_without_retry() -> None:
    """ADR-013 Section 3: 'retrying something nothing can execute is
    noise, not resilience' - terminal on the first attempt, despite
    attempts (1) being well under max_attempts (3)."""
    job = claimed_job(job_type="NOT_A_REAL_JOB_TYPE", attempts=1, max_attempts=3)
    factory, jobs, _handler = _wire(claimable=[job])

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={},
        )
    )

    assert jobs.failed_retryable == []
    assert len(jobs.failed_terminal) == 1
    assert jobs.failed_terminal[0].completed_at == _NOW
    assert jobs.failed_terminal[0].last_error is not None
    assert jobs.failed_terminal[0].last_error.startswith(NoHandlerRegisteredError.__name__)


# --- lease sweep (ADR-013 Section 5) ------------------------------------


def test_lease_sweep_uses_the_lease_duration_boundary() -> None:
    factory, jobs, _handler = _wire()

    asyncio.run(sweep_expired_leases(factory, clock=FrozenClock(_NOW)))  # type: ignore[arg-type]

    assert jobs.reclaim_calls == [_NOW - timedelta(seconds=LEASE_DURATION_SECONDS)]


def test_lease_sweep_returns_a_reclaimable_job_to_pending_with_backoff() -> None:
    """A lease expiry is functionally a crash, routed through the same
    Tx C decision as any other failure (ADR-013 Section 5)."""
    lease = ExpiredLease(job_id="stuck-1", job_type="RETENTION", attempts=1, max_attempts=3)
    factory, jobs, _handler = _wire(expired_leases=[lease])

    reclaimed = asyncio.run(
        sweep_expired_leases(factory, clock=FrozenClock(_NOW))  # type: ignore[arg-type]
    )

    assert reclaimed == 1
    assert len(jobs.failed_retryable) == 1
    assert jobs.failed_retryable[0].job_id == "stuck-1"
    assert jobs.failed_retryable[0].scheduled_for == _NOW + timedelta(seconds=BACKOFF_BASE_SECONDS)


def test_lease_sweep_fails_an_exhausted_job_terminally() -> None:
    lease = ExpiredLease(job_id="stuck-2", job_type="RETENTION", attempts=3, max_attempts=3)
    factory, jobs, _handler = _wire(expired_leases=[lease])

    asyncio.run(sweep_expired_leases(factory, clock=FrozenClock(_NOW)))  # type: ignore[arg-type]

    assert jobs.failed_retryable == []
    assert len(jobs.failed_terminal) == 1
    assert jobs.failed_terminal[0].job_id == "stuck-2"
    assert jobs.failed_terminal[0].completed_at == _NOW
    assert jobs.failed_terminal[0].last_error is not None
    assert "LeaseExpiredError" in jobs.failed_terminal[0].last_error


def test_lease_sweep_reclaim_and_bookkeeping_share_one_transaction() -> None:
    """The FOR UPDATE SKIP LOCKED lock reclaim_expired_leases takes only
    holds for its own transaction - marking in a second one would release
    the locks first."""
    lease = ExpiredLease(job_id="stuck-3", job_type="RETENTION", attempts=1, max_attempts=3)
    factory, _jobs, _handler = _wire(expired_leases=[lease])

    asyncio.run(sweep_expired_leases(factory, clock=FrozenClock(_NOW)))  # type: ignore[arg-type]

    assert factory.events == ["tx_open", "tx_commit", "tx_close"]


def test_poll_cycle_sweeps_expired_leases_before_claiming() -> None:
    """ADR-013 Section 5: the sweep runs at the top of the cycle, so a job
    abandoned by a crashed worker becomes claimable in the same cycle
    that may then claim it."""
    lease = ExpiredLease(job_id="stuck-4", job_type="RETENTION", attempts=1, max_attempts=3)
    job = claimed_job()
    factory, jobs, handler = _wire(claimable=[job], expired_leases=[lease])

    asyncio.run(
        run_poll_cycle(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    # The sweep's transaction opened and closed before the claim was asked
    # for at all.
    assert jobs.reclaim_calls != []
    assert jobs.claim_calls != []
    assert factory.events.index("tx_open") < factory.events.index("handler")
    assert factory.transactions_opened == 3  # sweep + claim + handler


# --- poll loop ----------------------------------------------------------


def test_run_poll_loop_honours_max_iterations() -> None:
    factory, jobs, _handler = _wire(claimable=[])

    asyncio.run(
        run_poll_loop(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={},
            poll_interval_seconds=0.0,
            max_iterations=3,
        )
    )

    # One sweep + one claim attempt per iteration.
    assert len(jobs.reclaim_calls) == 3
    assert len(jobs.claim_calls) == 3


def test_run_poll_loop_processes_each_queued_job_then_goes_idle() -> None:
    jobs_to_claim = [claimed_job(job_id="a"), claimed_job(job_id="b")]
    factory, jobs, handler = _wire(claimable=list(jobs_to_claim))

    asyncio.run(
        run_poll_loop(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={"RETENTION": handler},  # type: ignore[dict-item]
            poll_interval_seconds=0.0,
            max_iterations=3,
        )
    )

    assert [call.job_id for call in jobs.succeeded] == ["a", "b"]
    assert len(handler.calls) == 2
    # The third iteration found nothing.
    assert len(jobs.claim_calls) == 3


def test_run_poll_loop_with_zero_max_iterations_does_nothing() -> None:
    factory, jobs, _handler = _wire(claimable=[claimed_job()])

    asyncio.run(
        run_poll_loop(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={},
            poll_interval_seconds=0.0,
            max_iterations=0,
        )
    )

    assert jobs.claim_calls == []
    assert factory.events == []


# --- job-outcome metric (Phase 1 Step 13, observability foundation) -----


def _job_outcome_total(*, job_type: str, outcome: str) -> float:
    """`PLATFORM_REGISTRY` is a process-wide singleton the whole test
    suite shares, so a Counter's absolute value is order-dependent -
    every test here reads it before and after its own action and asserts
    the *delta*, never the absolute value, to stay independent of
    whatever earlier tests already incremented."""
    return (
        PLATFORM_REGISTRY.get_sample_value(
            "atp_worker_job_outcomes_total", {"job_type": job_type, "outcome": outcome}
        )
        or 0.0
    )


def test_run_once_increments_the_succeeded_outcome_counter() -> None:
    job = claimed_job(job_type="RETENTION")
    factory, _jobs, handler = _wire(claimable=[job])
    succeeded_before = _job_outcome_total(job_type="RETENTION", outcome="succeeded")
    failed_before = _job_outcome_total(job_type="RETENTION", outcome="failed")

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={job.job_type: handler},  # type: ignore[dict-item]
        )
    )

    assert _job_outcome_total(job_type="RETENTION", outcome="succeeded") - succeeded_before == 1.0
    # The failed counter for this same job_type must not also have moved.
    assert _job_outcome_total(job_type="RETENTION", outcome="failed") == failed_before


def test_run_once_increments_the_failed_outcome_counter_on_an_unknown_job_type() -> None:
    """Reuses the same unreachable-in-production path
    `test_unknown_job_type_fails_immediately_without_retry` already
    exercises - simplest reliable way to force the `except` branch
    without depending on `RecordingHandler`'s `raises` behavior."""
    job = claimed_job(job_type="AUDIT_INTEGRITY_CHECK")
    factory, _jobs, _handler = _wire(claimable=[job])
    before = _job_outcome_total(job_type="AUDIT_INTEGRITY_CHECK", outcome="failed")

    asyncio.run(
        run_once(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_NOW),
            id_generator=SequentialIdGenerator(),
            instance_id=_INSTANCE,
            registry={},  # no handler registered for any job_type
        )
    )

    after = _job_outcome_total(job_type="AUDIT_INTEGRITY_CHECK", outcome="failed")
    assert after - before == 1.0
