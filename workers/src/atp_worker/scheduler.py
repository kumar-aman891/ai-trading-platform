"""`atp_worker`'s sole producer into `core.job_queue` (ADR-013 §1, §6, §6a).

`ensure_recurring_jobs_scheduled` is a pure function of the injected
`Clock` - it consults no ledger state and remembers nothing between calls
(ADR-013 §6a: "this keeps `scheduler.py` stateless... missed windows are
never attested rather than caught up later"). Every decision this module
makes - whether to attempt an enqueue this tick, and with what payload -
is re-derived from `clock.now()` alone. `run_scheduler_loop` calls it once
per tick and sleeps between calls; the arithmetic never depends on how
long that sleep actually took, so a delayed or skipped tick changes what
gets attested, never how the next tick computes its target.

Deduplication is exactly `SqlAlchemyJobQueueRepository.enqueue_if_absent`
plus migration 0005's `ux_job_queue_one_live_per_type` partial unique
index - insert, let a collision raise `IntegrityError`, receive `False`
back. This module never lists or counts existing rows before deciding to
enqueue; introducing a second, Python-side dedup check would race the
database-enforced one for no benefit and contradict ADR-013 §6's explicit
"insert, catch `IntegrityError`, never check-then-insert" precedent.

`SCHEDULE_TICK_SECONDS` is a correctness constant, not a tuning knob, and
is therefore not exposed as a CLI flag or environment override the way
`atp_worker.runner.DEFAULT_POLL_INTERVAL_SECONDS` is. ADR-013 §6a's
coverage and re-attestation guarantees for `AUDIT_INTEGRITY_CHECK` hold
only if ticks actually occur every `SCHEDULE_TICK_SECONDS` and only while
`WINDOW_WIDTH_SECONDS` remains an exact multiple of it - changing the tick
cadence without also revisiting that arithmetic would silently break the
"attested three times, no gap, no overlap" guarantee. `run_scheduler_loop`
does expose a `tick_interval_seconds` parameter, but it controls only how
long the loop sleeps between calls (a test-only knob, mirroring
`run_poll_loop`'s own `poll_interval_seconds` / `max_iterations`
precedent) - it is never read by the window-selection arithmetic itself,
which always uses the fixed `SCHEDULE_TICK_SECONDS` constant regardless of
what the loop's sleep duration is set to.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_platform.logging import get_logger
from atp_platform.redaction import redact_text
from atp_worker.registry import (
    JOB_TYPE_AUDIT_INTEGRITY_CHECK,
    JOB_TYPE_RETENTION,
    JOB_TYPE_SESSION_REAP,
)
from atp_worker.uow import UnitOfWorkFactory

_logger = get_logger("atp_worker.scheduler")

#: ADR-013 §6a. The width of one `AUDIT_INTEGRITY_CHECK` window.
WINDOW_WIDTH_SECONDS = 900

#: ADR-013 §6/§6a. How often `ensure_recurring_jobs_scheduled` must be
#: called for its arithmetic to hold - see the module docstring for why
#: this is fixed rather than configurable.
SCHEDULE_TICK_SECONDS = 300

#: ADR-013 §6a: `WINDOW_WIDTH_SECONDS // SCHEDULE_TICK_SECONDS` - both the
#: number of ticks in one window and the number of times each window is
#: attested (first attestation plus this-minus-one re-checks). The §6a
#: properties hold only while this divides evenly; asserted, not merely
#: commented, so a future edit to either constant fails loudly rather than
#: silently producing a fractional re-check cycle.
RECHECK_CYCLE = WINDOW_WIDTH_SECONDS // SCHEDULE_TICK_SECONDS
assert WINDOW_WIDTH_SECONDS % SCHEDULE_TICK_SECONDS == 0, (
    "ADR-013 §6a requires WINDOW_WIDTH_SECONDS to be an exact multiple of " "SCHEDULE_TICK_SECONDS."
)

#: ADR-013 §2: "Cutoff is 7 days... once per day."
RETENTION_INTERVAL_SECONDS = 86400
_RETENTION_TICKS_PER_DAY = RETENTION_INTERVAL_SECONDS // SCHEDULE_TICK_SECONDS
assert RETENTION_INTERVAL_SECONDS % SCHEDULE_TICK_SECONDS == 0, (
    "RETENTION_INTERVAL_SECONDS must be an exact multiple of SCHEDULE_TICK_SECONDS "
    "for the once-per-day gate below to land on the same tick every day."
)

#: Mirrors `core.job_queue.max_attempts`'s own `server_default='3'`
#: (`persistence/src/atp_persistence/models/core.py`) - `enqueue_if_absent`
#: takes `max_attempts` explicitly rather than relying on a DB-side
#: default (ADR-013 §12: policy lives in the caller, not the repository),
#: so this scheduler states the same value it would have gotten anyway.
DEFAULT_MAX_ATTEMPTS = 3


def _epoch_seconds(moment: datetime) -> int:
    return int(moment.timestamp())


def _from_epoch_seconds(epoch_seconds: int) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC)


@dataclass(frozen=True, slots=True)
class _AuditIntegrityTarget:
    scheduled_for: datetime
    window_start: datetime
    window_end: datetime


def _resolve_audit_integrity_target(now: datetime) -> _AuditIntegrityTarget | None:
    """ADR-013 §6a's exact integer arithmetic, unmodified:

    ```
    tick_index    = floor(now_epoch_seconds / SCHEDULE_TICK_SECONDS)
    lag           = tick_index mod RECHECK_CYCLE                  # 0, 1, or 2
    newest_closed = floor(now_epoch_seconds / WINDOW_WIDTH_SECONDS) - 1
    target        = newest_closed - lag
    ```

    Returns `None` when `target < 0` - a system younger than one window,
    which §6a says must enqueue nothing this tick (not a future-facing
    window, which the handler itself would reject; a target that does not
    exist yet)."""
    now_epoch = _epoch_seconds(now)
    tick_index = now_epoch // SCHEDULE_TICK_SECONDS
    lag = tick_index % RECHECK_CYCLE
    newest_closed = now_epoch // WINDOW_WIDTH_SECONDS - 1
    target = newest_closed - lag
    if target < 0:
        return None

    window_start_epoch = target * WINDOW_WIDTH_SECONDS
    return _AuditIntegrityTarget(
        scheduled_for=_from_epoch_seconds(tick_index * SCHEDULE_TICK_SECONDS),
        window_start=_from_epoch_seconds(window_start_epoch),
        window_end=_from_epoch_seconds(window_start_epoch + WINDOW_WIDTH_SECONDS),
    )


def _is_retention_tick(now: datetime) -> bool:
    """True on exactly one tick per day - the one whose tick boundary
    falls on a UTC day boundary. `RETENTION`'s cadence (once per day, §2)
    is coarser than `SCHEDULE_TICK_SECONDS`, unlike `SESSION_REAP`'s,
    so - alone among the three job types - it needs a tick-skipping gate
    rather than attempting an enqueue on every call: without one, the
    partial unique index would only prevent overlap while a prior
    `RETENTION` row was still `PENDING`/`RUNNING`, and a job that finishes
    in seconds would otherwise be re-enqueued roughly every 5 minutes
    instead of once a day. Computed the same way as `AUDIT_INTEGRITY_CHECK`'s
    `tick_index` - a pure function of `now`, consulting no prior tick."""
    tick_index = _epoch_seconds(now) // SCHEDULE_TICK_SECONDS
    return tick_index % _RETENTION_TICKS_PER_DAY == 0


@dataclass(frozen=True, slots=True)
class SchedulingOutcome:
    """What `ensure_recurring_jobs_scheduled` actually did this tick - for
    logging and tests. `True` means a new row was inserted; `False` means
    either the tick did not attempt this job type (`RETENTION` outside its
    daily gate, or `AUDIT_INTEGRITY_CHECK` before any window has closed)
    or the attempt collided with an already-live row of that type."""

    audit_integrity_check_enqueued: bool
    retention_enqueued: bool
    session_reap_enqueued: bool


async def ensure_recurring_jobs_scheduled(
    uow_factory: UnitOfWorkFactory, *, clock: Clock, id_generator: IdGenerator
) -> SchedulingOutcome:
    """One scheduling tick, all three job types (ADR-013 §6). Runs in a
    single transaction - unlike `atp_worker.runner`'s three-transaction
    claim protocol (ADR-013 §3), which exists to bound a *handler's*
    blast radius, this module only ever calls the narrow, already
    collision-safe `enqueue_if_absent` (itself internally `SAVEPOINT`-
    isolated per call), so there is no handler execution here for a
    shared transaction to endanger.

    `now = clock.now()` is read exactly once and reused for every
    decision this tick makes, so `AUDIT_INTEGRITY_CHECK`'s target window,
    the `RETENTION` daily gate, and every `enqueue_if_absent` call agree
    on what instant this tick represents."""
    now = clock.now()

    audit_target = _resolve_audit_integrity_target(now)
    attempt_retention = _is_retention_tick(now)

    audit_enqueued = False
    retention_enqueued = False
    session_reap_enqueued = False

    async with uow_factory() as uow:
        if audit_target is not None:
            audit_enqueued = await uow.jobs.enqueue_if_absent(
                job_id=id_generator.new_id(),
                job_type=JOB_TYPE_AUDIT_INTEGRITY_CHECK,
                payload={
                    "window_start": audit_target.window_start.isoformat(),
                    "window_end": audit_target.window_end.isoformat(),
                },
                scheduled_for=audit_target.scheduled_for,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
                created_at=now,
            )

        if attempt_retention:
            retention_enqueued = await uow.jobs.enqueue_if_absent(
                job_id=id_generator.new_id(),
                job_type=JOB_TYPE_RETENTION,
                payload={},
                scheduled_for=now,
                max_attempts=DEFAULT_MAX_ATTEMPTS,
                created_at=now,
            )

        # SESSION_REAP's cadence (every 5 minutes, §2) equals
        # SCHEDULE_TICK_SECONDS exactly, so - unlike RETENTION - every
        # tick attempts an enqueue unconditionally; migration 0005's
        # partial unique index alone keeps this at roughly one live
        # SESSION_REAP row at a time.
        session_reap_enqueued = await uow.jobs.enqueue_if_absent(
            job_id=id_generator.new_id(),
            job_type=JOB_TYPE_SESSION_REAP,
            payload={},
            scheduled_for=now,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            created_at=now,
        )

    outcome = SchedulingOutcome(
        audit_integrity_check_enqueued=audit_enqueued,
        retention_enqueued=retention_enqueued,
        session_reap_enqueued=session_reap_enqueued,
    )
    _logger.info(
        "scheduler_tick",
        audit_integrity_check_enqueued=outcome.audit_integrity_check_enqueued,
        retention_enqueued=outcome.retention_enqueued,
        session_reap_enqueued=outcome.session_reap_enqueued,
    )
    return outcome


async def run_scheduler_loop(
    uow_factory: UnitOfWorkFactory,
    *,
    clock: Clock,
    id_generator: IdGenerator,
    tick_interval_seconds: float = SCHEDULE_TICK_SECONDS,
    max_iterations: int | None = None,
) -> None:
    """Calls `ensure_recurring_jobs_scheduled` once per tick and sleeps
    `tick_interval_seconds` between calls. `max_iterations` is test-only,
    mirroring `atp_worker.runner.run_poll_loop` exactly - production
    callers leave it `None` and run until the process is stopped.

    A tick that raises is logged and the loop continues: one failed
    scheduling attempt (e.g. a transient database error) must not stop
    the worker from claiming and executing whatever is already queued."""
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        try:
            await ensure_recurring_jobs_scheduled(
                uow_factory, clock=clock, id_generator=id_generator
            )
        except Exception as exc:  # a tick failure must not stop the loop
            _logger.error("scheduler_tick_failed", error=redact_text(str(exc)))
        iterations += 1
        await asyncio.sleep(tick_interval_seconds)
