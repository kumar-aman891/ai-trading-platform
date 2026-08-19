"""Unit tests for `atp_worker.scheduler` (Phase 1 Step 12 Phase B,
ADR-013 §6/§6a).

`RecordingUnitOfWorkFactory`/`FakeJobQueueRepository` (`tests/unit/worker/
fakes.py`) are the same fakes `test_runner.py` uses - `scheduler.py` only
ever touches `uow.jobs`, so `FakeWorkerUnitOfWork`'s jobs-only surface is
enough. `FakeJobQueueRepository.enqueue_if_absent` reproduces migration
0005's partial unique index with a plain `set[str]`; `complete_live(...)`
is the test-only way to simulate a previously-enqueued job of a type
reaching a terminal state, since production only clears that entry via a
claim + terminal update this module never performs.

Expected values for the arithmetic tests are computed by hand against
ADR-013 §6a's published formula (reproduced verbatim in comments), not by
re-deriving it in a parallel helper - the same style `test_runner.py`
uses for `backoff_delay` (hardcoded expected seconds per case), so a bug
in `scheduler.py`'s own arithmetic cannot be masked by a second copy of
the same bug in the test.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_worker.registry import (
    JOB_TYPE_AUDIT_INTEGRITY_CHECK,
    JOB_TYPE_RETENTION,
    JOB_TYPE_SESSION_REAP,
)
from atp_worker.scheduler import (
    RECHECK_CYCLE,
    SCHEDULE_TICK_SECONDS,
    WINDOW_WIDTH_SECONDS,
    ensure_recurring_jobs_scheduled,
    run_scheduler_loop,
)
from tests.unit.worker.fakes import FakeJobQueueRepository, RecordingUnitOfWorkFactory

# A window boundary that is also a day boundary (2026-01-01T00:00:00 UTC is
# midnight, hence a multiple of both 900 and 86400) - picked so every
# arithmetic property below is exercised without an epoch-alignment
# accident hiding a bug.
_ALIGNED_START = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def _wire() -> tuple[RecordingUnitOfWorkFactory, FakeJobQueueRepository]:
    jobs = FakeJobQueueRepository()
    return RecordingUnitOfWorkFactory(jobs=jobs), jobs


def _audit_payloads(jobs: FakeJobQueueRepository) -> list[dict[str, object]]:
    return [
        call.payload
        for call in jobs.enqueue_if_absent_calls
        if call.job_type == JOB_TYPE_AUDIT_INTEGRITY_CHECK
    ]


def _complete_all(jobs: FakeJobQueueRepository) -> None:
    jobs.complete_live(JOB_TYPE_AUDIT_INTEGRITY_CHECK)
    jobs.complete_live(JOB_TYPE_RETENTION)
    jobs.complete_live(JOB_TYPE_SESSION_REAP)


def test_constants_match_the_adr() -> None:
    assert WINDOW_WIDTH_SECONDS == 900
    assert SCHEDULE_TICK_SECONDS == 300
    assert RECHECK_CYCLE == 3


# --- AUDIT_INTEGRITY_CHECK window targeting (ADR-013 Section 6a) --------


def test_first_tick_targets_the_window_that_just_closed() -> None:
    """At a tick that lands exactly on a window boundary, the window that
    just closed is the target - the first and simplest case of Section
    6a's formula."""
    factory, jobs = _wire()

    asyncio.run(
        ensure_recurring_jobs_scheduled(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_ALIGNED_START),
            id_generator=SequentialIdGenerator(),
        )
    )

    [payload] = _audit_payloads(jobs)
    assert payload["window_start"] == "2025-12-31T23:45:00+00:00"
    assert payload["window_end"] == "2026-01-01T00:00:00+00:00"
    call = next(
        c for c in jobs.enqueue_if_absent_calls if c.job_type == JOB_TYPE_AUDIT_INTEGRITY_CHECK
    )
    assert call.scheduled_for == _ALIGNED_START


def test_nine_ticks_follow_the_exact_lag_rotation() -> None:
    """Nine consecutive ticks (45 minutes) from an aligned start, hand
    -computed against Section 6a's formula:

    tick_index = floor(now/300); lag = tick_index mod 3
    newest_closed = floor(now/900) - 1; target = newest_closed - lag

    Each entry is (window_start, window_end) as ISO-8601. Note the
    rotation is not a simple +15min walk: ticks 0,1,2 step *backwards*
    through the three most recently closed windows (lag 0,1,2), then tick
    3 (lag resets to 0, newest_closed has advanced) jumps to the window
    that just closed - the "no simple wall-clock approximation" Section
    6a warns about.
    """
    expected = [
        ("2025-12-31T23:45:00+00:00", "2026-01-01T00:00:00+00:00"),  # i=0
        ("2025-12-31T23:30:00+00:00", "2025-12-31T23:45:00+00:00"),  # i=1
        ("2025-12-31T23:15:00+00:00", "2025-12-31T23:30:00+00:00"),  # i=2
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:15:00+00:00"),  # i=3
        ("2025-12-31T23:45:00+00:00", "2026-01-01T00:00:00+00:00"),  # i=4
        ("2025-12-31T23:30:00+00:00", "2025-12-31T23:45:00+00:00"),  # i=5
        ("2026-01-01T00:15:00+00:00", "2026-01-01T00:30:00+00:00"),  # i=6
        ("2026-01-01T00:00:00+00:00", "2026-01-01T00:15:00+00:00"),  # i=7
        ("2025-12-31T23:45:00+00:00", "2026-01-01T00:00:00+00:00"),  # i=8 - 3rd hit
    ]

    factory, jobs = _wire()
    clock = FrozenClock(_ALIGNED_START)
    id_generator = SequentialIdGenerator()

    for _ in expected:
        asyncio.run(
            ensure_recurring_jobs_scheduled(factory, clock=clock, id_generator=id_generator)  # type: ignore[arg-type]
        )
        _complete_all(jobs)
        clock.advance(timedelta(seconds=SCHEDULE_TICK_SECONDS))

    actual = [(p["window_start"], p["window_end"]) for p in _audit_payloads(jobs)]
    assert actual == expected


def test_windows_are_covered_with_no_gaps_or_overlaps_and_attested_at_most_three_times() -> None:
    """Four hours (48 ticks) of continuous operation. Verifies, without
    hand-computing every tick:

    - every attested window is exactly 900 seconds wide;
    - the distinct window_start values tile the timeline with no gap and
      no overlap (Section 6a's "no gap and no overlap" - about window
      *bounds*, not attestation count, per the ADR's correction);
    - no window is ever attested more than three times - not merely that
      the *fully covered* interior windows reach three, but that nothing
      ever exceeds it, which is the property that makes the "surfaced
      once, clearly" comparison rule in Section 6a meaningful at all.
    """
    factory, jobs = _wire()
    clock = FrozenClock(_ALIGNED_START)
    id_generator = SequentialIdGenerator()
    total_ticks = 48  # 4 hours

    for _ in range(total_ticks):
        asyncio.run(
            ensure_recurring_jobs_scheduled(factory, clock=clock, id_generator=id_generator)  # type: ignore[arg-type]
        )
        _complete_all(jobs)
        clock.advance(timedelta(seconds=SCHEDULE_TICK_SECONDS))

    counts: Counter[tuple[str, str]] = Counter(
        (p["window_start"], p["window_end"]) for p in _audit_payloads(jobs)
    )

    for window_start_str, window_end_str in counts:
        start = datetime.fromisoformat(window_start_str)
        end = datetime.fromisoformat(window_end_str)
        assert end - start == timedelta(seconds=WINDOW_WIDTH_SECONDS)

    starts = sorted(datetime.fromisoformat(s) for s, _e in counts)
    for earlier, later in itertools.pairwise(starts):
        assert later - earlier == timedelta(
            seconds=WINDOW_WIDTH_SECONDS
        ), "gap or overlap between consecutive attested windows"

    assert max(counts.values()) == 3
    # The first two windows in the run (already mid-rotation when the run
    # started) and the last two (not yet re-checked when the run ended)
    # have not accumulated all three attestations - every window strictly
    # between them has.
    interior_starts = starts[2:-2]
    assert interior_starts  # sanity: the 4-hour run is long enough to have some
    for start in interior_starts:
        window_end = (start + timedelta(seconds=WINDOW_WIDTH_SECONDS)).isoformat()
        assert counts[(start.isoformat(), window_end)] == 3


def test_target_window_not_yet_available_enqueues_nothing() -> None:
    """A clock close enough to the epoch that no window has closed yet
    (`target < 0`, Section 6a) must not enqueue an AUDIT_INTEGRITY_CHECK
    job at all - not a future-facing window (the handler's own concern),
    a target that does not exist yet."""
    factory, jobs = _wire()

    asyncio.run(
        ensure_recurring_jobs_scheduled(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(datetime.fromtimestamp(0, tz=UTC) + timedelta(seconds=1)),
            id_generator=SequentialIdGenerator(),
        )
    )

    assert _audit_payloads(jobs) == []


def test_scheduler_does_not_backfill_windows_missed_during_a_gap() -> None:
    """ADR-013 Section 6a / Section 15: window selection is a pure
    function of `now` and consults no ledger state, so a scheduler that
    was not ticked for hours does not enumerate and enqueue everything it
    missed - it simply targets whatever is current at the moment it is
    next asked. This is what keeps `scheduler.py` stateless."""
    factory, jobs = _wire()
    clock = FrozenClock(_ALIGNED_START)
    id_generator = SequentialIdGenerator()

    asyncio.run(
        ensure_recurring_jobs_scheduled(factory, clock=clock, id_generator=id_generator)  # type: ignore[arg-type]
    )
    _complete_all(jobs)

    # A five-hour gap - as if the process was down, or simply never
    # ticked - with no intervening calls.
    clock.advance(timedelta(hours=5))
    asyncio.run(
        ensure_recurring_jobs_scheduled(factory, clock=clock, id_generator=id_generator)  # type: ignore[arg-type]
    )

    payloads = _audit_payloads(jobs)
    assert len(payloads) == 2  # one from before the gap, exactly one after
    # The post-gap call targets only the window closest to the new "now" -
    # not every window that closed during the five-hour gap.
    assert payloads[-1]["window_end"] == "2026-01-01T05:00:00+00:00"
    assert payloads[-1]["window_start"] == "2026-01-01T04:45:00+00:00"


# --- RETENTION (once per day) --------------------------------------------


def test_retention_is_attempted_only_on_the_daily_boundary_tick() -> None:
    factory, jobs = _wire()
    clock = FrozenClock(_ALIGNED_START)
    id_generator = SequentialIdGenerator()

    # A day is 288 ticks at the 300-second cadence - drive a handful
    # around the boundary rather than the full day for test speed.
    for _ in range(3):
        asyncio.run(
            ensure_recurring_jobs_scheduled(factory, clock=clock, id_generator=id_generator)  # type: ignore[arg-type]
        )
        _complete_all(jobs)
        clock.advance(timedelta(seconds=SCHEDULE_TICK_SECONDS))

    retention_calls = [c for c in jobs.enqueue_if_absent_calls if c.job_type == JOB_TYPE_RETENTION]
    assert len(retention_calls) == 1  # only the aligned first tick (i=0)


def test_retention_recurs_after_exactly_one_day_of_ticks() -> None:
    factory, jobs = _wire()
    clock = FrozenClock(_ALIGNED_START)
    id_generator = SequentialIdGenerator()
    ticks_per_day = 86400 // SCHEDULE_TICK_SECONDS

    for _ in range(ticks_per_day + 1):
        asyncio.run(
            ensure_recurring_jobs_scheduled(factory, clock=clock, id_generator=id_generator)  # type: ignore[arg-type]
        )
        _complete_all(jobs)
        clock.advance(timedelta(seconds=SCHEDULE_TICK_SECONDS))

    retention_calls = [c for c in jobs.enqueue_if_absent_calls if c.job_type == JOB_TYPE_RETENTION]
    # tick 0 and tick `ticks_per_day` both land on a day boundary.
    assert len(retention_calls) == 2
    assert retention_calls[0].scheduled_for == _ALIGNED_START
    assert retention_calls[1].scheduled_for == _ALIGNED_START + timedelta(days=1)


def test_retention_payload_is_empty_and_carries_no_window() -> None:
    """RETENTION's own cutoff is computed by the handler from the clock
    and its payload's retention_window_days (ADR-013 Section 2) - the
    scheduler supplies none of that, only an empty payload."""
    factory, jobs = _wire()

    asyncio.run(
        ensure_recurring_jobs_scheduled(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_ALIGNED_START),
            id_generator=SequentialIdGenerator(),
        )
    )

    [call] = [c for c in jobs.enqueue_if_absent_calls if c.job_type == JOB_TYPE_RETENTION]
    assert call.payload == {}


# --- SESSION_REAP (every tick) -------------------------------------------


def test_session_reap_is_attempted_every_tick() -> None:
    factory, jobs = _wire()
    clock = FrozenClock(_ALIGNED_START)
    id_generator = SequentialIdGenerator()

    for _ in range(5):
        asyncio.run(
            ensure_recurring_jobs_scheduled(factory, clock=clock, id_generator=id_generator)  # type: ignore[arg-type]
        )
        _complete_all(jobs)
        clock.advance(timedelta(seconds=SCHEDULE_TICK_SECONDS))

    session_reap_calls = [
        c for c in jobs.enqueue_if_absent_calls if c.job_type == JOB_TYPE_SESSION_REAP
    ]
    assert len(session_reap_calls) == 5
    assert all(c.payload == {} for c in session_reap_calls)


# --- collision handling via the existing repository semantics -----------


def test_collision_is_reported_through_enqueue_if_absent_not_a_second_dedup_check() -> None:
    """Pre-seeding a live row of each type and confirming the outcome
    reports `False` - via the same `enqueue_if_absent` collision path
    migration 0005's unique index backs - is what proves this module adds
    no Python-side "is one already scheduled?" check of its own."""
    factory, jobs = _wire()
    jobs._live_job_types = {  # test-only direct seed
        JOB_TYPE_AUDIT_INTEGRITY_CHECK,
        JOB_TYPE_RETENTION,
        JOB_TYPE_SESSION_REAP,
    }

    outcome = asyncio.run(
        ensure_recurring_jobs_scheduled(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_ALIGNED_START),
            id_generator=SequentialIdGenerator(),
        )
    )

    assert outcome.audit_integrity_check_enqueued is False
    assert outcome.retention_enqueued is False
    assert outcome.session_reap_enqueued is False
    # The attempt still happened - enqueue_if_absent was called and itself
    # reported the collision, rather than being skipped by a pre-check.
    assert len(jobs.enqueue_if_absent_calls) == 3


def test_successful_enqueue_reports_true_in_the_outcome() -> None:
    factory, _jobs = _wire()

    outcome = asyncio.run(
        ensure_recurring_jobs_scheduled(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_ALIGNED_START),
            id_generator=SequentialIdGenerator(),
        )
    )

    assert outcome.audit_integrity_check_enqueued is True
    assert outcome.retention_enqueued is True
    assert outcome.session_reap_enqueued is True


def test_each_enqueue_attempt_gets_a_freshly_minted_job_id() -> None:
    factory, jobs = _wire()

    asyncio.run(
        ensure_recurring_jobs_scheduled(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_ALIGNED_START),
            id_generator=SequentialIdGenerator(),
        )
    )

    job_ids = [c.job_id for c in jobs.enqueue_if_absent_calls]
    assert len(job_ids) == len(set(job_ids)) == 3


# --- run_scheduler_loop ---------------------------------------------------


def test_run_scheduler_loop_honours_max_iterations() -> None:
    factory, jobs = _wire()

    asyncio.run(
        run_scheduler_loop(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_ALIGNED_START),
            id_generator=SequentialIdGenerator(),
            tick_interval_seconds=0.0,
            max_iterations=3,
        )
    )

    # A FrozenClock never advances on its own, so all three iterations
    # see the same `now` - every call after the first collides via
    # enqueue_if_absent (nothing completed between calls) and reports
    # False, but the attempt itself still happens each iteration.
    audit_calls = [
        c for c in jobs.enqueue_if_absent_calls if c.job_type == JOB_TYPE_AUDIT_INTEGRITY_CHECK
    ]
    assert len(audit_calls) == 3


def test_run_scheduler_loop_with_zero_max_iterations_does_nothing() -> None:
    factory, jobs = _wire()

    asyncio.run(
        run_scheduler_loop(
            factory,  # type: ignore[arg-type]
            clock=FrozenClock(_ALIGNED_START),
            id_generator=SequentialIdGenerator(),
            tick_interval_seconds=0.0,
            max_iterations=0,
        )
    )

    assert jobs.enqueue_if_absent_calls == []


def test_run_scheduler_loop_continues_after_a_tick_raises() -> None:
    """A tick that fails (e.g. a transient database error opening the
    transaction) must not stop the loop - later ticks still run."""

    class _FailingOnceFactory:
        def __init__(self, inner: RecordingUnitOfWorkFactory) -> None:
            self._inner = inner
            self._raised = False

        def __call__(self) -> object:
            if not self._raised:
                self._raised = True
                raise RuntimeError("transient database error")
            return self._inner()

    factory, jobs = _wire()
    failing_once = _FailingOnceFactory(factory)

    asyncio.run(
        run_scheduler_loop(
            failing_once,  # type: ignore[arg-type]
            clock=FrozenClock(_ALIGNED_START),
            id_generator=SequentialIdGenerator(),
            tick_interval_seconds=0.0,
            max_iterations=2,
        )
    )

    audit_calls = [
        c for c in jobs.enqueue_if_absent_calls if c.job_type == JOB_TYPE_AUDIT_INTEGRITY_CHECK
    ]
    # The first iteration's failure produced no call; the second still ran.
    assert len(audit_calls) == 1


def test_run_scheduler_loop_reuses_the_clock_across_iterations_deterministically() -> None:
    """A sanity check that the loop itself never reaches for wall-clock
    time - only ever the injected Clock, advanced explicitly here between
    manual calls to the same effect a real 300s sleep would have."""
    factory, jobs = _wire()
    clock = FrozenClock(_ALIGNED_START)
    grouped: defaultdict[str, int] = defaultdict(int)

    for _ in range(4):
        asyncio.run(
            ensure_recurring_jobs_scheduled(
                factory,  # type: ignore[arg-type]
                clock=clock,
                id_generator=SequentialIdGenerator(),
            )
        )
        _complete_all(jobs)
        for payload in _audit_payloads(jobs)[-1:]:
            grouped[str(payload["window_start"])] += 1
        clock.advance(timedelta(seconds=SCHEDULE_TICK_SECONDS))

    # Four distinct ticks produced four distinct targets (no two adjacent
    # ticks in this range repeat before the lag rotation brings them back).
    assert len(grouped) == 4
