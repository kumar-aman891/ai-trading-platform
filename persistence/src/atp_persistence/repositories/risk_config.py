"""Read-only lookup of the active, immutable `core.risk_config` row
(docs/schemas/risk_config.md) for a given mode.

Persistence-layer only, no matching `atp_domain.ports.storage` Protocol -
Phase 1 has no route or process that ever mutates this table (a
trigger-enforced immutable table, per its own model docstring);
`atp_exec_paper` only ever reads the currently-active config.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.risk.config import RiskConfig
from atp_domain.types import Mode
from atp_persistence.mappers import row_to_risk_config
from atp_persistence.models.core import RiskConfigRow


class SqlAlchemyRiskConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active(self, mode: Mode) -> RiskConfig | None:
        result = await self._session.execute(
            select(RiskConfigRow).where(
                RiskConfigRow.mode == mode.value, RiskConfigRow.active.is_(True)
            )
        )
        row = result.scalar_one_or_none()
        return row_to_risk_config(row) if row is not None else None
