"""In-memory fakes for `atp_worker.runner`'s transaction and repository
dependencies.

`atp_worker.runner`'s whole job is *transaction choreography* - which
transaction opens when, what commits, what is still usable after a
rollback (ADR-013 §3). That is exactly what these fakes make observable:
`RecordingUnitOfWorkFactory` appends every open/commit/rollback/close to
one shared `events` list, so a test can assert on the actual ordering
rather than on a proxy for it.

`FakeJobQueueRepository` is shared across every unit of work the factory
produces, the way one real database is shared across successive
transactions - so a `mark_failed_terminal` written in Tx C is visible to
a test that also inspected what Tx A claimed.

What these fakes deliberately do **not** simulate: row locking,
`SKIP LOCKED`, constraint enforcement, or any other PostgreSQL behavior.
A test here can prove `runner` *asks* for a claim before running a
handler and opens a fresh transaction for failure bookkeeping; it cannot
and must not be read as proving concurrent claim exclusivity - that needs
the real database and belongs in the Docker-gated worker integration
suite (a later step).

Not a test file itself - no `test_*` function lives here, so pytest does
not collect it (mirrors `tests/unit/exec_paper/fakes.py`'s precedent).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import TracebackType

from atp_domain.audit import AuditEvent
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_persistence.repositories.audit_events import WindowAttestationStats
from atp_persistence.repositories.jobs import ClaimedJob, ExpiredLease
from atp_persistence.repositories.session_observations import SessionExpiryObservation


@dataclass
class MarkSucceededCall:
    job_id: str
    completed_at: datetime


@dataclass
class MarkFailedRetryableCall:
    job_id: str
    scheduled_for: datetime


@dataclass
class MarkFailedTerminalCall:
    job_id: str
    completed_at: datetime
    last_error: str | None


@dataclass
class EnqueueIfAbsentCall:
    job_id: str
    job_type: str
    payload: dict[str, object]
    scheduled_for: datetime
    max_attempts: int
    created_at: datetime


class FakeJobQueueRepository:
    """Duck-typed to `SqlAlchemyJobQueueRepository`'s method signatures.

    `claim_next` pops from `claimable` so a poll loop over several
    iterations sees a finite queue and then an empty one, exactly as the
    real repository would once every due job has been claimed.

    `enqueue_if_absent` reproduces migration 0005's
    `ux_job_queue_one_live_per_type` partial unique index with a plain
    `set[str]` of job types that currently have a live (PENDING/RUNNING)
    row - a collision returns `False`, exactly like the real repository's
    caught `IntegrityError`, without a test needing a real database to
    observe that behavior. Nothing here auto-completes a row: a test that
    wants a later tick to succeed again must call `complete_live(job_type)`
    itself, mirroring how only a claim-then-terminal-update in production
    would ever clear the real index's entry."""

    def __init__(
        self,
        *,
        claimable: list[ClaimedJob] | None = None,
        expired_leases: list[ExpiredLease] | None = None,
        delete_terminal_before_result: int = 0,
    ) -> None:
        self.claimable = list(claimable or [])
        self.expired_leases = list(expired_leases or [])
        self.claim_calls: list[tuple[datetime, str]] = []
        self.reclaim_calls: list[datetime] = []
        self.succeeded: list[MarkSucceededCall] = []
        self.failed_retryable: list[MarkFailedRetryableCall] = []
        self.failed_terminal: list[MarkFailedTerminalCall] = []
        self.delete_terminal_before_calls: list[datetime] = []
        self.delete_terminal_before_result = delete_terminal_before_result
        self.enqueue_if_absent_calls: list[EnqueueIfAbsentCall] = []
        self._live_job_types: set[str] = set()

    async def claim_next(self, *, now: datetime, instance_id: str) -> ClaimedJob | None:
        self.claim_calls.append((now, instance_id))
        if not self.claimable:
            return None
        return self.claimable.pop(0)

    async def mark_succeeded(self, job_id: str, *, completed_at: datetime) -> None:
        self.succeeded.append(MarkSucceededCall(job_id=job_id, completed_at=completed_at))

    async def mark_failed_retryable(self, job_id: str, *, scheduled_for: datetime) -> None:
        self.failed_retryable.append(
            MarkFailedRetryableCall(job_id=job_id, scheduled_for=scheduled_for)
        )

    async def mark_failed_terminal(
        self, job_id: str, *, completed_at: datetime, last_error: str | None
    ) -> None:
        self.failed_terminal.append(
            MarkFailedTerminalCall(job_id=job_id, completed_at=completed_at, last_error=last_error)
        )

    async def reclaim_expired_leases(self, *, older_than: datetime) -> Sequence[ExpiredLease]:
        self.reclaim_calls.append(older_than)
        reclaimed, self.expired_leases = self.expired_leases, []
        return reclaimed

    async def delete_terminal_before(self, *, cutoff: datetime) -> int:
        self.delete_terminal_before_calls.append(cutoff)
        return self.delete_terminal_before_result

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
        self.enqueue_if_absent_calls.append(
            EnqueueIfAbsentCall(
                job_id=job_id,
                job_type=job_type,
                payload=payload,
                scheduled_for=scheduled_for,
                max_attempts=max_attempts,
                created_at=created_at,
            )
        )
        if job_type in self._live_job_types:
            return False
        self._live_job_types.add(job_type)
        return True

    def complete_live(self, job_type: str) -> None:
        """Test-only: simulates the live row of `job_type` reaching a
        terminal state, clearing the way for the next `enqueue_if_absent`
        of that type to succeed. Not part of `SqlAlchemyJobQueueRepository`'s
        real interface - production clears this by claiming and marking
        the row terminal, which this fake does not model row-by-row."""
        self._live_job_types.discard(job_type)


@dataclass
class FakeWorkerUnitOfWork:
    """Exposes only `jobs` - the sole repository `atp_worker.runner`
    itself touches. `audit`/`audit_events`/`session_observations` are a
    real `WorkerUnitOfWork`'s other three attributes, used by handlers,
    not by the runtime core under test here - `FakeHandlerUnitOfWork`
    below is the handler-level equivalent that exposes all four."""

    jobs: FakeJobQueueRepository


class FakeAuditEventWriter:
    """Duck-typed to `SqlAlchemyAuditEventWriter`."""

    def __init__(self) -> None:
        self.saved: list[AuditEvent] = []

    async def save(self, event: AuditEvent) -> None:
        self.saved.append(event)


class FakeAuditEventRepository:
    """Duck-typed to `SqlAlchemyAuditEventRepository`.

    `stats_by_window` is keyed by `(window_start.isoformat(),
    window_end.isoformat())` - a test configures exactly the aggregate
    `window_attestation_stats` should report for the window it is
    exercising."""

    def __init__(
        self,
        *,
        recent: list[AuditEvent] | None = None,
        stats_by_window: dict[tuple[str, str], WindowAttestationStats] | None = None,
    ) -> None:
        self.recent = list(recent or [])
        self._stats_by_window = dict(stats_by_window or {})
        self.list_recent_calls: list[tuple[str | None, int]] = []
        self.window_stats_calls: list[tuple[datetime, datetime]] = []

    async def list_recent(
        self,
        *,
        mode: object = None,
        action: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[AuditEvent]:
        self.list_recent_calls.append((action, limit))
        matching = [event for event in self.recent if action is None or event.action == action]
        return matching[:limit]

    async def window_attestation_stats(
        self, *, window_start: datetime, window_end: datetime
    ) -> WindowAttestationStats:
        self.window_stats_calls.append((window_start, window_end))
        key = (window_start.isoformat(), window_end.isoformat())
        return self._stats_by_window[key]


class FakeSessionObservationRepository:
    """Duck-typed to `SqlAlchemyWorkerSessionObservationRepository`. No
    method here can mutate a session - matching the real class, whose own
    API makes that structurally impossible, not merely undesired."""

    def __init__(self, *, observations: list[SessionExpiryObservation] | None = None) -> None:
        self.observations = list(observations or [])
        self.calls: list[datetime] = []

    async def list_expired_unrevoked(self, *, now: datetime) -> Sequence[SessionExpiryObservation]:
        self.calls.append(now)
        return list(self.observations)


@dataclass
class FakeHandlerUnitOfWork:
    """The handler-level double: all four attributes a real
    `WorkerUnitOfWork` exposes, for testing `atp_worker.handlers.*`
    directly rather than through `runner`'s transaction choreography
    (which `FakeWorkerUnitOfWork`/`RecordingUnitOfWorkFactory` above
    already cover)."""

    jobs: FakeJobQueueRepository
    audit: FakeAuditEventWriter
    audit_events: FakeAuditEventRepository
    session_observations: FakeSessionObservationRepository


class _RecordingTransaction:
    """One `async with uow_factory()` block. Records its own lifecycle so
    a test can assert exactly where a transaction opened and closed
    relative to the handler running."""

    def __init__(self, factory: RecordingUnitOfWorkFactory) -> None:
        self._factory = factory

    async def __aenter__(self) -> FakeWorkerUnitOfWork:
        self._factory.events.append("tx_open")
        self._factory.open_transactions += 1
        return FakeWorkerUnitOfWork(jobs=self._factory.jobs)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # Mirrors `worker_unit_of_work`'s real contract: roll back on any
        # exception (and let it propagate), commit on a clean exit.
        self._factory.events.append("tx_rollback" if exc_type is not None else "tx_commit")
        self._factory.events.append("tx_close")
        self._factory.open_transactions -= 1
        return False


@dataclass
class RecordingUnitOfWorkFactory:
    """A `UnitOfWorkFactory` (zero-argument, returns an async context
    manager) that records transaction lifecycle events."""

    jobs: FakeJobQueueRepository
    events: list[str] = field(default_factory=list)
    open_transactions: int = 0

    def __call__(self) -> _RecordingTransaction:
        return _RecordingTransaction(self)

    @property
    def transactions_opened(self) -> int:
        return self.events.count("tx_open")


class RecordingHandler:
    """A `JobHandler` that records each invocation - and, critically, how
    many transactions were open at the moment it ran, which is what
    proves the claim transaction had already closed."""

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self._raises = raises
        self.calls: list[ClaimedJob] = []
        self.open_transactions_when_called: list[int] = []
        self.factory: RecordingUnitOfWorkFactory | None = None

    async def __call__(
        self,
        uow: FakeWorkerUnitOfWork,
        job: ClaimedJob,
        *,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self.calls.append(job)
        if self.factory is not None:
            self.factory.events.append("handler")
            self.open_transactions_when_called.append(self.factory.open_transactions)
        if self._raises is not None:
            raise self._raises


def claimed_job(
    *,
    job_id: str = "job-1",
    job_type: str = "RETENTION",
    attempts: int = 1,
    max_attempts: int = 3,
    payload: dict[str, object] | None = None,
) -> ClaimedJob:
    return ClaimedJob(
        job_id=job_id,
        job_type=job_type,
        payload=payload if payload is not None else {},
        attempts=attempts,
        max_attempts=max_attempts,
    )
