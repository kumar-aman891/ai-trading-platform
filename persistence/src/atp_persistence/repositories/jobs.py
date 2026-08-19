"""Storage layer for `core.job_queue` - the claim protocol's persistence
side (ADR-013 "Operational Worker Scope").

No backoff formula, lease duration, or scheduling cadence lives here.
ADR-013 §12 fixes the time source: every "now" the claim protocol uses is
read from `atp_worker.runner`'s injected `Clock` (not yet implemented),
never from inside this module - so every method that needs "now" takes it
as a parameter, and every method that needs a *future* time (a retry's
`scheduled_for`, a lease boundary) takes that as a parameter too, already
computed by the caller. This class only executes the SQL a policy decision
translates into; it makes no policy decisions itself.

Three transactions, never one (ADR-013 §3) - this repository's methods are
deliberately narrow so a caller cannot accidentally combine them into a
single transaction the way ADR-013 explicitly rejects:
    - `claim_next` is Tx A alone: it claims a row and increments
      `attempts`. It does not run a handler and does not set any terminal
      state.
    - `mark_succeeded` is Tx B's terminal half: the handler's own work and
      its audit write (where applicable) are the caller's responsibility,
      in the same transaction as this call - this repository has no
      handler-invocation logic to accidentally fuse with it.
    - `mark_failed_retryable` / `mark_failed_terminal` are Tx C, run in a
      fresh transaction after Tx B's rollback - never call these from
      inside the same transaction as `claim_next` or a handler's work.

`attempts`/`max_attempts` bounds and the terminal/`completed_at` pairing
are enforced by migration 0005's CHECK constraints
(`ck_job_queue_attempts_within_bounds`,
`ck_job_queue_terminal_state_has_completed_at`) - this module does not
duplicate those checks in Python; a caller that violates them gets a real
`sqlalchemy.exc.IntegrityError` from the database, not a silently
different Python-side error.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from atp_persistence.models.core import JobQueueRow

_STATUS_PENDING = "PENDING"
_STATUS_RUNNING = "RUNNING"
_STATUS_SUCCEEDED = "SUCCEEDED"
_STATUS_FAILED = "FAILED"
_TERMINAL_STATUSES = (_STATUS_SUCCEEDED, _STATUS_FAILED)


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """A `core.job_queue` row immediately after `claim_next` - a
    persistence-level read projection, not a domain type (mirrors
    `KillSwitchStateSnapshot`'s precedent: no domain type exists for a job
    row, and none should - a job is operational state, not trading
    domain, per ADR-013's own framing)."""

    job_id: str
    job_type: str
    payload: dict[str, object]
    attempts: int
    max_attempts: int


@dataclass(frozen=True, slots=True)
class ExpiredLease:
    """A `core.job_queue` row `reclaim_expired_leases` found RUNNING past
    its lease boundary. Carries enough for the caller to decide
    PENDING-vs-FAILED (ADR-013 §5) without a second read - `attempts` is
    already incremented from the claim that produced this stuck row, so
    the caller compares it against `max_attempts` directly."""

    job_id: str
    job_type: str
    attempts: int
    max_attempts: int


class SqlAlchemyJobQueueRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_next(self, *, now: datetime, instance_id: str) -> ClaimedJob | None:
        """ADR-013 Tx A: `SELECT ... FOR UPDATE SKIP LOCKED` over due
        PENDING rows, oldest-`scheduled_for`-first with `job_id` as a
        deterministic tiebreak (both are ordinary index/PK reads - no
        privilege beyond `atp_worker`'s full grant on `core.job_queue` is
        needed, unlike `atp_paper_exec`'s narrower situation on
        `paper.trade_proposals`, ADR-011). Increments `attempts` and marks
        the row RUNNING before returning it - a crash after this method
        returns still costs exactly one attempt (ADR-013 §3), never zero.
        Returns `None` if no due row is available; does not raise for an
        empty queue."""
        result = await self._session.execute(
            select(JobQueueRow)
            .where(JobQueueRow.status == _STATUS_PENDING, JobQueueRow.scheduled_for <= now)
            .order_by(JobQueueRow.scheduled_for, JobQueueRow.job_id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        row.status = _STATUS_RUNNING
        row.attempts = row.attempts + 1
        row.locked_at = now
        row.locked_by = instance_id
        await self._session.flush()

        return ClaimedJob(
            job_id=row.job_id,
            job_type=row.job_type,
            payload=row.payload,
            attempts=row.attempts,
            max_attempts=row.max_attempts,
        )

    async def mark_succeeded(self, job_id: str, *, completed_at: datetime) -> None:
        """ADR-013 Tx B's terminal half - the handler's work and its
        audit write (where applicable, never for SESSION_REAP) are the
        caller's responsibility in the same transaction as this call.
        A no-op if `job_id` is unknown (mirrors
        `SqlAlchemySessionRepository.revoke`'s existing idempotent-no-op
        precedent for an already-gone row) rather than raising."""
        row = await self._session.get(JobQueueRow, job_id)
        if row is not None:
            row.status = _STATUS_SUCCEEDED
            row.completed_at = completed_at
            row.locked_at = None
            row.locked_by = None

    async def mark_failed_retryable(self, job_id: str, *, scheduled_for: datetime) -> None:
        """ADR-013 Tx C, under-`max_attempts` branch: returns the job to
        PENDING at a caller-computed retry time - the backoff formula
        (ADR-013 §4) lives in `atp_worker.runner`, not here. Never sets
        `completed_at`: a retryable failure is not terminal
        (`ck_job_queue_terminal_state_has_completed_at` would reject a
        row that tried to claim otherwise)."""
        row = await self._session.get(JobQueueRow, job_id)
        if row is not None:
            row.status = _STATUS_PENDING
            row.scheduled_for = scheduled_for
            row.locked_at = None
            row.locked_by = None

    async def mark_failed_terminal(
        self, job_id: str, *, completed_at: datetime, last_error: str | None
    ) -> None:
        """ADR-013 Tx C, exhausted branch (also used for an unknown
        `job_type`, ADR-013 §3): moves the job to FAILED - terminal, no
        retry. `last_error` is expected to already be redacted and
        truncated by the caller (ADR-013 §8, `atp_platform.redaction`);
        this method stores exactly what it is given, unmodified."""
        row = await self._session.get(JobQueueRow, job_id)
        if row is not None:
            row.status = _STATUS_FAILED
            row.completed_at = completed_at
            row.locked_at = None
            row.locked_by = None
            row.last_error = last_error

    async def reclaim_expired_leases(self, *, older_than: datetime) -> Sequence[ExpiredLease]:
        """ADR-013 §5's lease sweep: RUNNING rows whose `locked_at`
        predates the caller-supplied lease boundary (`now() -
        LEASE_DURATION`, computed by `runner.py`) are functionally
        crashed claims. `FOR UPDATE SKIP LOCKED` so concurrent sweepers
        never block each other. Locks and returns each candidate but does
        not itself decide PENDING-vs-FAILED - that depends on
        `max_attempts`, which the returned `ExpiredLease` already
        carries, so the caller routes each one through the same Tx C
        failure path as any other failure (`mark_failed_retryable` /
        `mark_failed_terminal`) without a second read."""
        result = await self._session.execute(
            select(JobQueueRow)
            .where(JobQueueRow.status == _STATUS_RUNNING, JobQueueRow.locked_at < older_than)
            .with_for_update(skip_locked=True)
        )
        return [
            ExpiredLease(
                job_id=row.job_id,
                job_type=row.job_type,
                attempts=row.attempts,
                max_attempts=row.max_attempts,
            )
            for row in result.scalars().all()
        ]

    async def enqueue_if_absent(
        self,
        *,
        job_id: str,
        job_type: str,
        payload: dict[str, object],
        scheduled_for: datetime,
        max_attempts: int,
        created_at: datetime,
    ) -> bool:
        """ADR-013 §6: `scheduler.py`'s sole producer path - one
        pending-or-running row per `job_type` is enforced by migration
        0005's partial unique index `ux_job_queue_one_live_per_type`, not
        by a check-then-insert race here. Runs the insert in a
        `SAVEPOINT` (`begin_nested`) so a collision rolls back only the
        failed insert, not whatever transaction the caller is already
        inside - unlike every other insert-then-catch call site in this
        codebase (e.g. `atp_exec_paper.gateway.run_once` catching
        `IntegrityError` on `paper.risk_decisions`), this method's own
        contract is to swallow the collision and report it as a plain
        `bool`, because `scheduler.py`'s recurring-singleton scheduling
        has no outcome to react to beyond "did a job get queued or not."
        Every other IntegrityError in this module propagates unswallowed.

        Returns `True` if a new row was inserted, `False` if a
        PENDING-or-RUNNING row of this `job_type` already existed."""
        try:
            async with self._session.begin_nested():
                self._session.add(
                    JobQueueRow(
                        job_id=job_id,
                        job_type=job_type,
                        payload=payload,
                        status=_STATUS_PENDING,
                        attempts=0,
                        max_attempts=max_attempts,
                        scheduled_for=scheduled_for,
                        completed_at=None,
                        created_at=created_at,
                    )
                )
                await self._session.flush()
        except IntegrityError:
            return False
        return True

    async def delete_terminal_before(self, *, cutoff: datetime) -> int:
        """ADR-013's `RETENTION` job type: deletes `core.job_queue`'s own
        terminal rows (SUCCEEDED/FAILED) completed before `cutoff`. The
        WHERE clause makes "never touches a PENDING/RUNNING row"
        structural, not merely intended - including the RETENTION job's
        own row, which is RUNNING while this executes. Returns the number
        of rows deleted."""
        result = await self._session.execute(
            delete(JobQueueRow).where(
                JobQueueRow.status.in_(_TERMINAL_STATUSES),
                JobQueueRow.completed_at < cutoff,
            )
        )
        # `AsyncSession.execute()` is typed to return the generic
        # `Result[Any]`, but a Core DELETE construct always yields a
        # `CursorResult` at runtime, which is what actually carries
        # `.rowcount` - the standard SQLAlchemy pattern for reading a
        # DML row count through the ORM-enabled `execute()` call.
        return cast(CursorResult[Any], result).rowcount
