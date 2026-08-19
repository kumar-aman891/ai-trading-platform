"""`RETENTION` handler (ADR-013 §2).

Prunes `core.job_queue`'s **own** terminal rows -
`uow.jobs.delete_terminal_before` issues
`DELETE ... WHERE status IN ('SUCCEEDED','FAILED') AND completed_at < :cutoff`
(`atp_persistence.repositories.jobs`). That `WHERE` clause is what makes
"never deletes a PENDING/RUNNING row" structural rather than a promise
this handler keeps by convention - including this job's own row, which is
`RUNNING` while it executes and therefore excluded by the same predicate.

Not a data-retention or compliance policy: no such policy exists anywhere
in this repository (ADR-013 §2), and `atp_worker` holds `DELETE` on no
table other than `core.job_queue`. This is job-table housekeeping only -
the self-scheduling design (ADR-013 §6) means `core.job_queue` otherwise
accumulates terminal rows unboundedly.

Genuinely idempotent (ADR-013 §7): the cutoff is a pure function of the
payload (or the default) and the clock, so a second run against the same
already-pruned state deletes zero additional rows. No audit event: job
lifecycle is operational state, not something this immutable ledger
should carry (ADR-010, restated in ADR-013 §11's docstring for why no
JOB_STARTED/SUCCEEDED/FAILED constants exist).
"""

from __future__ import annotations

from datetime import timedelta

from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_persistence.repositories.jobs import ClaimedJob
from atp_platform.logging import get_logger
from atp_worker.errors import HandlerFailedError
from atp_worker.uow import WorkerUnitOfWork

_logger = get_logger("atp_worker.handlers.retention")

#: ADR-013 §2: "Cutoff is 7 days... read from the job's own payload if
#: present, defaulting to 7 if absent."
RETENTION_WINDOW_DAYS_DEFAULT = 7


def _resolve_retention_window_days(payload: dict[str, object]) -> int:
    raw = payload.get("retention_window_days", RETENTION_WINDOW_DAYS_DEFAULT)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise HandlerFailedError(
            f"job payload key 'retention_window_days' must be an integer, got {raw!r}.",
            retryable=False,
        )
    if raw <= 0:
        raise HandlerFailedError(
            f"job payload key 'retention_window_days' must be positive, got {raw!r}.",
            retryable=False,
        )
    return raw


async def retention_handler(
    uow: WorkerUnitOfWork, job: ClaimedJob, *, clock: Clock, id_generator: IdGenerator
) -> None:
    window_days = _resolve_retention_window_days(job.payload)
    cutoff = clock.now() - timedelta(days=window_days)

    deleted = await uow.jobs.delete_terminal_before(cutoff=cutoff)

    _logger.info(
        "job_queue_retention_applied",
        job_id=job.job_id,
        retention_window_days=window_days,
        cutoff=cutoff.isoformat(),
        rows_deleted=deleted,
    )
