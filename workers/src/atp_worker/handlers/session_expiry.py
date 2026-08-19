"""`SESSION_REAP` handler (ADR-013 §2).

**Observation only.** Counts sessions matching `expires_at < now() AND
revoked_at IS NULL` via `uow.session_observations.list_expired_unrevoked` -
the narrow, three-column projection
(`SqlAlchemyWorkerSessionObservationRepository`), never
`SqlAlchemySessionRepository`, whose `select(SessionRow)` would request
all seven columns and raise `InsufficientPrivilege` under the real
`atp_worker` role (`0003_table_grants.py`'s column-scoped grant).

No session mutation of any kind - no `save`/`revoke`/`delete` call, and
none is structurally possible here: `atp_worker` holds no INSERT/UPDATE/
DELETE grant on `core.sessions` to back one, and
`SqlAlchemyWorkerSessionObservationRepository` exposes no such method to
call even if it did. "Session reaping" language describing this job as
actually revoking sessions (`docs/schemas/session.md`'s earlier wording)
is corrected by ADR-013 §2, not repeated here - there is no security gap
this leaves open: `validate_and_renew_session` already refuses an expired
session regardless of `revoked_at`.

No audit event either - an observation is not a state change (ADR-010's
operational-vs-audit split, restated for this job type in ADR-013 §2).

Metric emission: ADR-013 §2 says this job "emits one structured log line
plus a metric." The structured log line below satisfies that. The metric
half is deliberately not wired to a concrete `atp_platform.metrics`
counter here - that module's own docstring states no Phase 1 service
emits any concrete metric yet ("the `/metrics` route and the first real
counters are Phase 1 Step 13+"). Registering the first one from inside a
handler in this step would contradict that documented sequencing. Flagged
here rather than silently resolved either way; wiring a real counter is
this handler's one remaining piece of ADR-013 §2, deferred to whichever
step turns metrics on.
"""

from __future__ import annotations

from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_persistence.repositories.jobs import ClaimedJob
from atp_platform.logging import get_logger
from atp_worker.uow import WorkerUnitOfWork

_logger = get_logger("atp_worker.handlers.session_expiry")


async def session_reap_handler(
    uow: WorkerUnitOfWork, job: ClaimedJob, *, clock: Clock, id_generator: IdGenerator
) -> None:
    observations = await uow.session_observations.list_expired_unrevoked(now=clock.now())

    _logger.info(
        "session_reap_observed",
        job_id=job.job_id,
        expired_unrevoked_count=len(observations),
    )
