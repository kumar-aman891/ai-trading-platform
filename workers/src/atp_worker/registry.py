"""Job-type -> handler mapping (ADR-013 §11).

`HANDLER_REGISTRY` must contain exactly the three keys `core.job_queue`'s
`valid_job_type` CHECK constraint allows, and nothing else. This module
holds the *code* side of that pair as three explicit constants; the
*database* side stays where it already is, in
`atp_persistence.models.core.JobQueueRow.__table_args__`. Safety invariant
#17 (a later step) asserts the two are equal **bidirectionally**, parsing
the allowlist out of the constraint's SQL text rather than restating it,
so neither side can drift without a test failing.

Two independent declarations plus a mechanical parity check is the point,
not an oversight: deriving these constants *from* the CHECK constraint at
import time would make that safety test tautological - it would assert a
value against itself and pass no matter how wrong both were. It would also
make a production import path depend on parsing SQL, which is worse than
the duplication it removes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_persistence.repositories.jobs import ClaimedJob
from atp_worker.handlers.audit_integrity import audit_integrity_check_handler
from atp_worker.handlers.retention import retention_handler
from atp_worker.handlers.session_expiry import session_reap_handler
from atp_worker.uow import WorkerUnitOfWork

#: The code-side job-type allowlist. Mirrors - and is checked against -
#: `JobQueueRow.__table_args__`'s `valid_job_type` CHECK constraint.
JOB_TYPE_SESSION_REAP = "SESSION_REAP"
JOB_TYPE_AUDIT_INTEGRITY_CHECK = "AUDIT_INTEGRITY_CHECK"
JOB_TYPE_RETENTION = "RETENTION"


class JobHandler(Protocol):
    """What a job handler is, structurally.

    Receives an already-open `WorkerUnitOfWork` and does **not** manage a
    transaction itself: ADR-013 §3's Tx B requires the handler's work, its
    audit write (where applicable), and the job's terminal-state update to
    commit together, which only `runner` - the owner of that transaction -
    can arrange. A handler that committed or rolled back on its own would
    break that guarantee, so it is not given the means to.

    Raising `HandlerFailedError` selects the retry behavior (see
    `atp_worker.errors`); any other exception is treated as a retryable
    failure. `id_generator` is threaded through for the one thing a
    handler cannot get elsewhere: a fresh `EventId` for any
    `atp_domain.audit.AuditEvent` it writes - mirrors
    `atp_exec_paper.gateway`'s `clock`/`id_generator` pairing exactly.
    """

    async def __call__(
        self,
        uow: WorkerUnitOfWork,
        job: ClaimedJob,
        *,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None: ...


HANDLER_REGISTRY: Mapping[str, JobHandler] = {
    JOB_TYPE_SESSION_REAP: session_reap_handler,
    JOB_TYPE_AUDIT_INTEGRITY_CHECK: audit_integrity_check_handler,
    JOB_TYPE_RETENTION: retention_handler,
}
