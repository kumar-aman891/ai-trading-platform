"""Concrete implementation of `atp_domain.ports.storage.AuditEventRepository`
(Phase 1 Step 7: the read-only audit browsing API foundation).

Read-only by construction - this class has no `save`/`insert` method, so no
router or service can reach for one. Writing an audit event happens only as
part of the state-changing transaction that produced it (ADR-010), not
through this repository.

`window_attestation_stats` (Phase 1 Step 12 Phase B, ADR-013 §2) is the one
addition since Step 7: `AUDIT_INTEGRITY_CHECK`'s window attestation needs a
genuine SQL aggregate - `count(*)`, `max(event_id)`, `max(recorded_at)` over
a window - and `list_recent`'s row-fetch-then-count-in-Python would
silently undercount any window wider than `_MAX_LIMIT`, corrupting the one
mechanism this handler exists to make trustworthy. Still read-only: no
`save`/`insert` method exists here or ever should.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.audit import AuditEvent
from atp_domain.types import Mode
from atp_persistence.mappers import row_to_audit_event
from atp_persistence.models.audit import AuditEventRow

_MAX_LIMIT = 200


@dataclass(frozen=True, slots=True)
class WindowAttestationStats:
    """The three values ADR-013 §2 defines a window attestation as -
    nothing else. `max_event_id`/`max_recorded_at` are `None` only when
    `observed_count` is `0` (an empty window has no maximum)."""

    observed_count: int
    max_event_id: str | None
    max_recorded_at: datetime | None


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

    async def window_attestation_stats(
        self, *, window_start: datetime, window_end: datetime
    ) -> WindowAttestationStats:
        """`count(*)`, `max(event_id)`, `max(recorded_at)` over
        `[window_start, window_end)` - a closed, past window supplied by
        the caller (`atp_worker.handlers.audit_integrity`, ADR-013 §2).

        `event_id` is cast to text before `max()` because **PostgreSQL has
        no `max(uuid)` aggregate** before version 18, and this project
        targets `postgres:16` (`docker-compose.test.yml`). The `uuid` type
        does support ordering *comparison* (so `ORDER BY event_id` is
        legal), which is what makes the missing aggregate easy to assume
        into existence - an earlier revision of this docstring did exactly
        that, and `function max(uuid) does not exist` was raised the first
        time this ran against real PostgreSQL (Phase 1 Step 12 Phase B).

        The cast is exact, not an approximation: a canonical uuid renders
        as fixed-position lowercase hex with hyphens at identical offsets,
        and for hex digits ASCII order (`0`-`9` then `a`-`f`) matches
        nibble order - so lexicographic text ordering is byte-for-byte
        equivalent to PostgreSQL's own `uuid` comparison. Every
        `event_id` is a UUIDv7, so that ordering is also time ordering.
        `as_uuid=False` (`models/base.py`) already hands Python a `str`
        either way, so the cast changes no value the caller observes."""
        result = await self._session.execute(
            select(
                func.count(AuditEventRow.event_id),
                func.max(cast(AuditEventRow.event_id, Text)),
                func.max(AuditEventRow.recorded_at),
            ).where(
                AuditEventRow.occurred_at >= window_start,
                AuditEventRow.occurred_at < window_end,
            )
        )
        count, max_event_id, max_recorded_at = result.one()
        return WindowAttestationStats(
            observed_count=count, max_event_id=max_event_id, max_recorded_at=max_recorded_at
        )
