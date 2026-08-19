"""The `atp_worker` claim/execute loop (ADR-013 §3-§6).

Structurally mirrors `atp_exec_paper.gateway`'s `run_once` /
`run_poll_cycle` / `run_poll_loop` trio - same per-item exception
isolation, same sleep-only-when-nothing-was-found behavior,
same test-only `max_iterations`.

Where it deliberately differs: ADR-013 §3 requires **three separate
transactions** per job, so every function here that opens one takes a
`UnitOfWorkFactory` and opens exactly one, visibly:

    Tx A  `_claim_next_job`      - claim, `attempts += 1`, commit, close.
    Tx B  `_execute_claimed_job` - handler work + audit + terminal state.
    Tx C  `_record_failure`      - a *fresh* transaction, opened only
                                   after Tx B has already rolled back and
                                   closed.

The ordering matters and is not incidental. `_claim_next_job` returns only
after its transaction has committed and closed, so no handler ever runs
while the claim transaction is open - a handler that blocks would
otherwise hold the claimed row's lock for its entire duration. And because
`attempts` increments in Tx A rather than Tx B, a process that is killed
mid-handler still costs exactly one attempt: Tx B's rollback cannot undo a
commit that already happened in Tx A. That is what stops a poison job -
one whose handler reliably kills the process - from cycling forever.

Tx C exists as a separate function for the same reason: once Tx B has
raised, its unit of work is rolled back and unusable, so failure
bookkeeping cannot be written through it. It needs a transaction of its
own.

All policy ADR-013 fixes as a constant lives here, not in
`atp_persistence.repositories.jobs` - the repository executes SQL and
holds no lease duration, backoff curve, or poll cadence. Every "now" comes
from the injected `Clock` (ADR-013 §12); this module never calls
`datetime.now()`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import timedelta

from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_persistence.repositories.jobs import ClaimedJob
from atp_platform.logging import get_logger
from atp_platform.redaction import redact_text
from atp_worker.errors import (
    HandlerFailedError,
    LeaseExpiredError,
    NoHandlerRegisteredError,
    WorkerError,
)
from atp_worker.registry import HANDLER_REGISTRY, JobHandler
from atp_worker.uow import UnitOfWorkFactory

_logger = get_logger("atp_worker.runner")

#: ADR-013 §6. Overridden per-process via ATP_WORKER_POLL_INTERVAL_SECONDS
#: by the entrypoint (a later step), never read from the environment here.
DEFAULT_POLL_INTERVAL_SECONDS = 5.0

#: ADR-013 §5. An application constant, deliberately not a stored column -
#: nothing in Phase 1 needs a per-job lease duration.
LEASE_DURATION_SECONDS = 300

#: ADR-013 §4: backoff(attempts) = min(BASE * 2 ** (attempts - 1), CAP).
#: No jitter - jitter desynchronizes competing instances, and Phase 1 runs
#: a single worker.
BACKOFF_BASE_SECONDS = 5
BACKOFF_CAP_SECONDS = 300

#: ADR-013 §8. Applied by this module after redaction; `last_error` itself
#: remains an untruncated `text` column.
MAX_LAST_ERROR_LENGTH = 2000


def backoff_delay(attempts: int) -> timedelta:
    """ADR-013 §4. `attempts` is the count *after* the claim increment, so
    the first failure (attempts == 1) waits `BACKOFF_BASE_SECONDS`."""
    exponent = max(0, attempts - 1)
    seconds = min(BACKOFF_BASE_SECONDS * (2**exponent), BACKOFF_CAP_SECONDS)
    return timedelta(seconds=seconds)


def format_last_error(exc: BaseException) -> str:
    """ADR-013 §8: exception class plus redacted message, truncated -
    never a traceback. Runs through `atp_platform.redaction.redact_text`,
    the same pipeline the log processors use, so a secret-shaped substring
    in an exception message cannot reach `core.job_queue.last_error` (a
    row an operator reads) any more than it can reach a log line."""
    return f"{type(exc).__name__}: {redact_text(str(exc))}"[:MAX_LAST_ERROR_LENGTH]


def _is_retryable(exc: BaseException) -> bool:
    """`NoHandlerRegisteredError` is terminal (ADR-013 §3), an explicitly
    non-retryable `HandlerFailedError` is terminal, and anything else -
    including an arbitrary exception a handler let escape - is retryable,
    because this module cannot know otherwise."""
    if isinstance(exc, NoHandlerRegisteredError):
        return False
    if isinstance(exc, HandlerFailedError):
        return exc.retryable
    return True


async def _claim_next_job(
    uow_factory: UnitOfWorkFactory, *, clock: Clock, instance_id: str
) -> ClaimedJob | None:
    """**Tx A.** Opens one transaction, claims at most one due job, and
    commits - returning only once that transaction has closed, so the row
    lock `SELECT ... FOR UPDATE SKIP LOCKED` took is released before any
    handler runs."""
    async with uow_factory() as uow:
        return await uow.jobs.claim_next(now=clock.now(), instance_id=instance_id)


async def _execute_claimed_job(
    uow_factory: UnitOfWorkFactory,
    job: ClaimedJob,
    *,
    clock: Clock,
    id_generator: IdGenerator,
    registry: Mapping[str, JobHandler],
) -> None:
    """**Tx B.** The handler's work, whatever audit event it writes, and
    the job's `SUCCEEDED`/`completed_at` update all commit together
    (ADR-013 §3, safety invariant #14). Any exception propagates after the
    unit of work has rolled back - the caller opens Tx C to record it."""
    async with uow_factory() as uow:
        handler = registry.get(job.job_type)
        if handler is None:
            raise NoHandlerRegisteredError(
                f"No handler registered for job_type {job.job_type!r} " f"(job_id={job.job_id})."
            )
        await handler(uow, job, clock=clock, id_generator=id_generator)
        await uow.jobs.mark_succeeded(job.job_id, completed_at=clock.now())


async def _record_failure(
    uow_factory: UnitOfWorkFactory,
    *,
    job_id: str,
    attempts: int,
    max_attempts: int,
    exc: BaseException,
    clock: Clock,
) -> None:
    """**Tx C.** A fresh transaction, opened only after Tx B has already
    rolled back and closed - never the same unit of work, which is
    unusable once its transaction has been rolled back.

    Retryable and under `max_attempts` -> back to `PENDING` at
    `now + backoff(attempts)`. Otherwise -> `FAILED` with `completed_at`,
    which is terminal and alertable (ADR-013 §3: no `DEAD_LETTER` state)."""
    now = clock.now()
    retryable = _is_retryable(exc)
    async with uow_factory() as uow:
        if retryable and attempts < max_attempts:
            await uow.jobs.mark_failed_retryable(
                job_id, scheduled_for=now + backoff_delay(attempts)
            )
        else:
            await uow.jobs.mark_failed_terminal(
                job_id, completed_at=now, last_error=format_last_error(exc)
            )


async def sweep_expired_leases(uow_factory: UnitOfWorkFactory, *, clock: Clock) -> int:
    """ADR-013 §5's lease sweep, run at the top of every poll cycle in its
    own transaction. A `RUNNING` row whose `locked_at` predates
    `now - LEASE_DURATION_SECONDS` is functionally a crashed claim, and is
    routed through the same retry/terminal decision as any other failure.

    Reclaim and bookkeeping share one transaction on purpose: the
    repository's `reclaim_expired_leases` takes a `FOR UPDATE SKIP LOCKED`
    lock on each row it returns, and that lock only holds for as long as
    the transaction that took it. Marking the rows in a second transaction
    would release the locks first and reintroduce exactly the race the
    lock exists to prevent.

    Returns the number of leases reclaimed."""
    now = clock.now()
    boundary = now - timedelta(seconds=LEASE_DURATION_SECONDS)
    async with uow_factory() as uow:
        expired = await uow.jobs.reclaim_expired_leases(older_than=boundary)
        for lease in expired:
            if lease.attempts < lease.max_attempts:
                await uow.jobs.mark_failed_retryable(
                    lease.job_id, scheduled_for=now + backoff_delay(lease.attempts)
                )
            else:
                await uow.jobs.mark_failed_terminal(
                    lease.job_id,
                    completed_at=now,
                    last_error=format_last_error(
                        LeaseExpiredError(
                            f"Lease expired after {LEASE_DURATION_SECONDS}s with "
                            f"{lease.attempts}/{lease.max_attempts} attempts used."
                        )
                    ),
                )
    if expired:
        _logger.warning(
            "reclaimed_expired_leases",
            count=len(expired),
            lease_duration_seconds=LEASE_DURATION_SECONDS,
        )
    return len(expired)


async def run_once(
    uow_factory: UnitOfWorkFactory,
    *,
    clock: Clock,
    id_generator: IdGenerator,
    instance_id: str,
    registry: Mapping[str, JobHandler] = HANDLER_REGISTRY,
) -> bool:
    """Claim at most one job (Tx A) and execute it (Tx B), recording any
    failure in a fresh transaction (Tx C). Returns `True` if a job was
    claimed - regardless of whether it went on to succeed - and `False` if
    the queue held nothing due, which `run_poll_cycle` uses to decide
    whether to sleep."""
    job = await _claim_next_job(uow_factory, clock=clock, instance_id=instance_id)
    if job is None:
        return False

    try:
        await _execute_claimed_job(
            uow_factory, job, clock=clock, id_generator=id_generator, registry=registry
        )
    except Exception as exc:
        _logger.warning(
            "job_failed",
            job_id=job.job_id,
            job_type=job.job_type,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            retryable=_is_retryable(exc),
            error=format_last_error(exc),
        )
        await _record_failure(
            uow_factory,
            job_id=job.job_id,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            exc=exc,
            clock=clock,
        )
    else:
        _logger.info("job_succeeded", job_id=job.job_id, job_type=job.job_type)
    return True


async def run_poll_cycle(
    uow_factory: UnitOfWorkFactory,
    *,
    clock: Clock,
    id_generator: IdGenerator,
    instance_id: str,
    registry: Mapping[str, JobHandler] = HANDLER_REGISTRY,
) -> bool:
    """One sweep-then-claim pass. The lease sweep runs **first** (ADR-013
    §5), so a job abandoned by a crashed worker becomes claimable again in
    the same cycle that might then claim it, rather than waiting for the
    next one. Returns whatever `run_once` returned."""
    await sweep_expired_leases(uow_factory, clock=clock)
    return await run_once(
        uow_factory,
        clock=clock,
        id_generator=id_generator,
        instance_id=instance_id,
        registry=registry,
    )


async def run_poll_loop(
    uow_factory: UnitOfWorkFactory,
    *,
    clock: Clock,
    id_generator: IdGenerator,
    instance_id: str,
    registry: Mapping[str, JobHandler] = HANDLER_REGISTRY,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_iterations: int | None = None,
) -> None:
    """The poll loop. `max_iterations` is test-only - production callers
    leave it `None` and run until the process is stopped (mirrors
    `atp_exec_paper.gateway.run_poll_loop` exactly).

    A `WorkerError` escaping a single cycle is logged and the loop
    continues: one job's failure must not stop the worker. Note that
    `run_once` already handles a failing *handler* itself, so reaching
    this only means the bookkeeping transaction itself failed."""
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        try:
            found_any = await run_poll_cycle(
                uow_factory,
                clock=clock,
                id_generator=id_generator,
                instance_id=instance_id,
                registry=registry,
            )
        except WorkerError as exc:
            _logger.error("poll_cycle_failed", error=format_last_error(exc))
            found_any = False
        iterations += 1
        if not found_any:
            await asyncio.sleep(poll_interval_seconds)
