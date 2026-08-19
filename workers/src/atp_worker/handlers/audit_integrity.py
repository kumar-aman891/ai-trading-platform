"""`AUDIT_INTEGRITY_CHECK` handler (ADR-013 §2).

Window attestation, **not hash chaining**. Over a closed, past window
taken from the job's own payload, this handler computes
`(count(*), max(event_id), max(recorded_at))` via
`SqlAlchemyAuditEventRepository.window_attestation_stats` and writes it as
a new, immutable `ACTION_AUDIT_INTEGRITY_ATTESTED` row - an `INSERT`, never
an `UPDATE`, matching the append-only ledger this handler holds only
`SELECT, INSERT` on (`audit.audit_events`, migration
`roles_and_schemas.sql.tmpl`). A later run over the *same* window (payload
window bounds match a prior attestation's) compares its freshly observed
values against that prior attestation, read back from the ledger itself -
not against any second, independent store. A mismatch means the window's
audit events changed after being attested: something disappeared, or
something was backdated into an already-closed window.

There is no `prev_hash` column and this module introduces no cryptographic
linkage between attestation rows. That capability - a genuine hash chain -
is explicitly deferred to Phase 4 under ADR-010; nothing here should be
read as a step toward it.

A detected mismatch is a **successful check**, not a failed job: this
handler never raises for one. It writes an additional
`ACTION_AUDIT_INTEGRITY_VIOLATION_DETECTED` event alongside the attestation
and returns normally, so `atp_worker.runner` marks the job `SUCCEEDED`.
Raising here would retry a genuine tamper signal up to `max_attempts`
times and bury it in `last_error` instead of surfacing it once, clearly,
in the audit ledger where an operator would look for it.
"""

from __future__ import annotations

from datetime import datetime

from atp_domain.audit import (
    ACTION_AUDIT_INTEGRITY_ATTESTED,
    ACTION_AUDIT_INTEGRITY_VIOLATION_DETECTED,
    AuditEvent,
)
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.types import ActorType, EventId
from atp_persistence.repositories.jobs import ClaimedJob
from atp_platform.correlation import new_correlation_id
from atp_platform.logging import get_logger
from atp_worker.errors import HandlerFailedError
from atp_worker.uow import WorkerUnitOfWork

_logger = get_logger("atp_worker.handlers.audit_integrity")

#: How many prior ACTION_AUDIT_INTEGRITY_ATTESTED rows to search for one
#: matching this window's bounds. AUDIT_INTEGRITY_CHECK runs on a fixed
#: 15-minute cadence (ADR-013 §6), so this comfortably covers several
#: days of history - generous for a re-check of a recent window without
#: fetching the whole ledger.
_PRIOR_ATTESTATION_SEARCH_LIMIT = 200


def _parse_window_bound(payload: dict[str, object], key: str) -> datetime:
    raw = payload.get(key)
    if not isinstance(raw, str):
        raise HandlerFailedError(
            f"job payload key {key!r} must be an ISO-8601 string, got {raw!r}.",
            retryable=False,
        )
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HandlerFailedError(
            f"job payload key {key!r}={raw!r} is not a valid ISO-8601 datetime: {exc}",
            retryable=False,
        ) from exc
    if value.tzinfo is None:
        raise HandlerFailedError(
            f"job payload key {key!r}={raw!r} must be timezone-aware.", retryable=False
        )
    return value


def _stringify(value: object) -> str:
    """Every `AuditEvent.source_refs` value is `str`
    (`atp_domain.audit.AuditEvent.source_refs: Mapping[str, str]`) - a
    non-string observed value is stringified at the call site, exactly as
    ADR-013 §9 requires and as every other numeric/UUID value already
    flowing through `source_refs` elsewhere in this codebase already is."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def audit_integrity_check_handler(
    uow: WorkerUnitOfWork, job: ClaimedJob, *, clock: Clock, id_generator: IdGenerator
) -> None:
    window_start = _parse_window_bound(job.payload, "window_start")
    window_end = _parse_window_bound(job.payload, "window_end")
    if window_end <= window_start:
        raise HandlerFailedError(
            f"window_end ({window_end.isoformat()}) must be after "
            f"window_start ({window_start.isoformat()}).",
            retryable=False,
        )
    now = clock.now()
    if window_end > now:
        # ADR-013 §2: "a closed past window" - scheduler.py (a later step)
        # is responsible for only ever enqueuing a window that has already
        # closed. A future-facing window reaching this handler is a
        # scheduling defect, not a transient condition retrying could fix.
        raise HandlerFailedError(
            f"window_end ({window_end.isoformat()}) is not in the past "
            f"relative to now ({now.isoformat()}) - AUDIT_INTEGRITY_CHECK "
            "only attests closed windows.",
            retryable=False,
        )

    stats = await uow.audit_events.window_attestation_stats(
        window_start=window_start, window_end=window_end
    )

    window_start_str = window_start.isoformat()
    window_end_str = window_end.isoformat()

    prior_attestations = await uow.audit_events.list_recent(
        action=ACTION_AUDIT_INTEGRITY_ATTESTED, limit=_PRIOR_ATTESTATION_SEARCH_LIMIT
    )
    prior = next(
        (
            event
            for event in prior_attestations
            if event.source_refs.get("window_start") == window_start_str
            and event.source_refs.get("window_end") == window_end_str
        ),
        None,
    )

    correlation_id = new_correlation_id()
    recorded_at = clock.now()

    # `max_recorded_at` is included here even though ADR-013 §9's key list
    # names six keys, not seven: §2's own description of what this handler
    # attests is explicitly "(count(*), max(event_id), max(recorded_at))" -
    # three values - and the "detects... backdating into an attested
    # window" claim in that same section is only provable if the attested
    # max(recorded_at) is actually stored to compare against. Omitting it
    # would make that claim false. Flagged here rather than silently
    # resolved either way.
    attested_source_refs = {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "window_start": window_start_str,
        "window_end": window_end_str,
        "observed_count": _stringify(stats.observed_count),
        "max_event_id": _stringify(stats.max_event_id),
        "max_recorded_at": _stringify(stats.max_recorded_at),
    }

    await uow.audit.save(
        AuditEvent(
            event_id=EventId(id_generator.new_id()),
            correlation_id=correlation_id,
            occurred_at=recorded_at,
            recorded_at=recorded_at,
            actor_type=ActorType.SYSTEM,
            actor_id=f"atp_worker/{job.job_id}",
            action=ACTION_AUDIT_INTEGRITY_ATTESTED,
            mode=None,
            strategy_id=None,
            strategy_version=None,
            instrument_id=None,
            source_refs=attested_source_refs,
        )
    )

    if prior is None:
        _logger.info(
            "audit_integrity_attested",
            job_id=job.job_id,
            window_start=window_start_str,
            window_end=window_end_str,
            observed_count=stats.observed_count,
            first_attestation=True,
        )
        return

    mismatch = (
        prior.source_refs.get("observed_count") != attested_source_refs["observed_count"]
        or prior.source_refs.get("max_event_id") != attested_source_refs["max_event_id"]
        or prior.source_refs.get("max_recorded_at") != attested_source_refs["max_recorded_at"]
    )
    if not mismatch:
        _logger.info(
            "audit_integrity_attested",
            job_id=job.job_id,
            window_start=window_start_str,
            window_end=window_end_str,
            observed_count=stats.observed_count,
            first_attestation=False,
            matches_prior_attestation=True,
        )
        return

    _logger.error(
        "audit_integrity_violation_detected",
        job_id=job.job_id,
        window_start=window_start_str,
        window_end=window_end_str,
        attested_observed_count=prior.source_refs.get("observed_count"),
        current_observed_count=attested_source_refs["observed_count"],
        attested_max_event_id=prior.source_refs.get("max_event_id"),
        current_max_event_id=attested_source_refs["max_event_id"],
    )
    await uow.audit.save(
        AuditEvent(
            event_id=EventId(id_generator.new_id()),
            correlation_id=correlation_id,
            occurred_at=clock.now(),
            recorded_at=clock.now(),
            actor_type=ActorType.SYSTEM,
            actor_id=f"atp_worker/{job.job_id}",
            action=ACTION_AUDIT_INTEGRITY_VIOLATION_DETECTED,
            mode=None,
            strategy_id=None,
            strategy_version=None,
            instrument_id=None,
            source_refs={
                "job_id": job.job_id,
                "job_type": job.job_type,
                "window_start": window_start_str,
                "window_end": window_end_str,
                "attested_observed_count": _stringify(prior.source_refs.get("observed_count")),
                "current_observed_count": attested_source_refs["observed_count"],
                "attested_max_event_id": _stringify(prior.source_refs.get("max_event_id")),
                "current_max_event_id": attested_source_refs["max_event_id"],
                "attested_max_recorded_at": _stringify(prior.source_refs.get("max_recorded_at")),
                "current_max_recorded_at": attested_source_refs["max_recorded_at"],
            },
        )
    )
    # No exception raised: ADR-013 §2 - a detected violation is a
    # successful check. atp_worker.runner marks this job SUCCEEDED.
