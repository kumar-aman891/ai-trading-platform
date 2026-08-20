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
plus a metric." The structured log line below satisfies the first half;
`_EXPIRED_UNREVOKED_GAUGE` (Phase 1 Step 13, observability foundation)
satisfies the second. A `Gauge`, not a `Counter`: each run reports the
count observed *this* run, not a running total - the value legitimately
goes up or down between runs. Set unconditionally on every run, including
zero, so an operator can distinguish "genuinely zero expired sessions"
from "this job stopped running" (a `Gauge` that stalls at a stale nonzero
value is itself the signal for the latter).

The metric name is `atp_worker_session_reap_expired_unrevoked_count`, not
the dotted `atp_worker.session_reap.expired_unrevoked_count` ADR-013 §2
writes in prose: Prometheus metric names are conventionally
`[a-zA-Z_:][a-zA-Z0-9_:]*` - underscore-separated, no dots - and while
`prometheus_client` does not itself reject a dotted name at construction
time, emitting one would be non-standard for any consumer expecting the
conventional exposition format. The underscored name is what ADR-013's
dotted prose always meant; this is a formatting translation, not a
naming decision made here.
"""

from __future__ import annotations

from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_persistence.repositories.jobs import ClaimedJob
from atp_platform.logging import get_logger
from atp_platform.metrics import gauge
from atp_worker.uow import WorkerUnitOfWork

_logger = get_logger("atp_worker.handlers.session_expiry")

_EXPIRED_UNREVOKED_GAUGE = gauge(
    "atp_worker_session_reap_expired_unrevoked_count",
    "Sessions with expires_at in the past and revoked_at IS NULL, as of the most recent "
    "SESSION_REAP run (ADR-013 Section 2).",
)


async def session_reap_handler(
    uow: WorkerUnitOfWork, job: ClaimedJob, *, clock: Clock, id_generator: IdGenerator
) -> None:
    observations = await uow.session_observations.list_expired_unrevoked(now=clock.now())

    _EXPIRED_UNREVOKED_GAUGE.set(len(observations))
    _logger.info(
        "session_reap_observed",
        job_id=job.job_id,
        expired_unrevoked_count=len(observations),
    )
