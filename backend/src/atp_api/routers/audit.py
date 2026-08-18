"""`GET /api/v1/audit/events` - read-only, paginated.

Filtering is restricted to `mode` and `action` (indexed together via
`ix_audit_events_mode_action_occurred_at`, docs/schemas/audit_event.md) -
no filter on `payload`, `source_refs`, or any other unindexed/free-form
column. Pagination is keyset (`before`), not offset, so it stays stable as
new events are appended (append-only table, ADR-010).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from atp_api.deps import get_audit_event_repository, require_permission
from atp_api.schemas.audit import AuditEventPage, AuditEventResponse
from atp_api.security.rbac import Permission
from atp_api.services.audit import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, list_audit_events
from atp_domain.types import Mode
from atp_persistence.repositories import SqlAlchemyAuditEventRepository

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get(
    "/events",
    response_model=AuditEventPage,
    dependencies=[Depends(require_permission(Permission.READ_AUDIT))],
)
async def get_audit_events(
    repository: Annotated[SqlAlchemyAuditEventRepository, Depends(get_audit_event_repository)],
    mode: Annotated[Literal["PAPER", "LIVE"] | None, Query()] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> AuditEventPage:
    page = await list_audit_events(
        repository,
        mode=Mode(mode) if mode is not None else None,
        action=action,
        before=before,
        limit=limit,
    )
    return AuditEventPage(
        items=[
            AuditEventResponse(
                event_id=item.event_id,
                correlation_id=item.correlation_id,
                occurred_at=item.occurred_at,
                actor_type=item.actor_type,
                action=item.action,
                mode=item.mode,
                decision=item.decision,
                instrument_id=item.instrument_id,
                risk_rule_ids=list(item.risk_rule_ids),
                error_code=item.error_code,
                error_class=item.error_class,
                source_refs=item.source_refs,
            )
            for item in page.items
        ],
        next_before=page.next_before,
        limit=page.limit,
    )
