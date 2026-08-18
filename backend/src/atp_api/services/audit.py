"""`GET /api/v1/audit/events` application logic - read-only, paginated.

`source_refs` is passed back through `atp_platform.redaction.redact_mapping`
before this function returns, even though it is already redacted at write
time (docs/schemas/audit_event.md) - defense-in-depth, matching the
platform-wide rule that redaction is a pipeline stage applied as close to
every exit point as practical, not trusted to have happened exactly once
upstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atp_domain.audit import AuditEvent
from atp_domain.types import Mode
from atp_persistence.repositories import SqlAlchemyAuditEventRepository
from atp_platform.redaction import redact_mapping

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class AuditEventView:
    event_id: str
    correlation_id: str
    occurred_at: datetime
    actor_type: str
    action: str
    mode: str | None
    decision: str | None
    instrument_id: str | None
    risk_rule_ids: tuple[str, ...]
    error_code: str | None
    error_class: str | None
    source_refs: dict[str, str]


@dataclass(frozen=True, slots=True)
class AuditEventPageView:
    items: tuple[AuditEventView, ...]
    next_before: datetime | None
    limit: int


def _to_view(event: AuditEvent) -> AuditEventView:
    return AuditEventView(
        event_id=str(event.event_id),
        correlation_id=event.correlation_id,
        occurred_at=event.occurred_at,
        actor_type=event.actor_type.value,
        action=event.action,
        mode=event.mode.value if event.mode is not None else None,
        decision=event.decision,
        instrument_id=str(event.instrument_id) if event.instrument_id is not None else None,
        risk_rule_ids=event.risk_rule_ids,
        error_code=event.error_code,
        error_class=event.error_class,
        source_refs=redact_mapping(event.source_refs),
    )


async def list_audit_events(
    repository: SqlAlchemyAuditEventRepository,
    *,
    mode: Mode | None,
    action: str | None,
    before: datetime | None,
    limit: int,
) -> AuditEventPageView:
    bounded_limit = max(1, min(limit, MAX_PAGE_SIZE))
    events = await repository.list_recent(
        mode=mode, action=action, before=before, limit=bounded_limit
    )
    items = tuple(_to_view(event) for event in events)
    next_before = items[-1].occurred_at if len(items) == bounded_limit else None
    return AuditEventPageView(items=items, next_before=next_before, limit=bounded_limit)
