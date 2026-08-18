"""Read-only kill-switch state query support (Phase 1 Step 7: the
kill-switch API foundation).

`KillSwitchStateSnapshot` is a persistence-level read projection, not a
domain type - it mirrors `core.kill_switch_state`'s stored columns exactly
(`engaged` is the raw stored boolean). It is deliberately *not* added to
`atp_domain.ports.storage` as a repository Protocol: interpreting a stored
row (three-state ENGAGED/DISENGAGED/UNAVAILABLE fail-closed policy) is
`atp_domain.killswitch`'s job, not this query's - this class only reads
what is currently stored, unchanged. No `save`/`engage`/`disengage` method
exists here; Step 7 is read-only foundation only (mutation is deferred to
Step 8 authentication/RBAC, per the kill-switch API foundation scope note).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_persistence.models.core import KillSwitchStateRow


@dataclass(frozen=True, slots=True)
class KillSwitchStateSnapshot:
    switch_id: str
    engaged: bool
    updated_at: datetime
    updated_by: str | None
    reason: str | None


class SqlAlchemyKillSwitchStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> Sequence[KillSwitchStateSnapshot]:
        result = await self._session.execute(
            select(KillSwitchStateRow).order_by(KillSwitchStateRow.switch_id)
        )
        return [
            KillSwitchStateSnapshot(
                switch_id=row.switch_id,
                engaged=row.engaged,
                updated_at=row.updated_at,
                updated_by=row.updated_by,
                reason=row.reason,
            )
            for row in result.scalars().all()
        ]
