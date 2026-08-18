"""Concrete implementation of `atp_domain.ports.storage.AuditEventRepository`
(Phase 1 Step 7: the read-only audit browsing API foundation).

Read-only by construction - this class has no `save`/`insert` method, so no
router or service can reach for one. Writing an audit event happens only as
part of the state-changing transaction that produced it (ADR-010), not
through this repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.audit import AuditEvent
from atp_domain.types import Mode
from atp_persistence.mappers import row_to_audit_event
from atp_persistence.models.audit import AuditEventRow

_MAX_LIMIT = 200


class SqlAlchemyAuditEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_recent(
        self,
        *,
        mode: Mode | None = None,
        action: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[AuditEvent]:
        bounded_limit = max(1, min(limit, _MAX_LIMIT))

        stmt = select(AuditEventRow).order_by(
            AuditEventRow.occurred_at.desc(), AuditEventRow.event_id.desc()
        )
        if mode is not None:
            stmt = stmt.where(AuditEventRow.mode == mode.value)
        if action is not None:
            stmt = stmt.where(AuditEventRow.action == action)
        if before is not None:
            stmt = stmt.where(AuditEventRow.occurred_at < before)
        stmt = stmt.limit(bounded_limit)

        result = await self._session.execute(stmt)
        return [row_to_audit_event(row) for row in result.scalars().all()]
