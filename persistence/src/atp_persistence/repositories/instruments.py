"""Read-only lookup of `core.instruments` (docs/schemas/instrument.md).

Persistence-layer only, no matching `atp_domain.ports.storage` Protocol -
`atp_domain.ports.broker.InstrumentRecord` is a distinct, broker-facing
shape reserved for a future real loader (that module's own docstring);
this class exists only to give `atp_exec_paper.risk_runner` the
`lot_size`/`tick_size` values `RuleContext` needs, read from whatever is
actually seeded (Phase 1: migration 0002's ~20 FIXTURE rows).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from atp_persistence.models.core import InstrumentRow


@dataclass(frozen=True, slots=True)
class InstrumentSnapshot:
    instrument_id: str
    symbol: str
    lot_size: int
    tick_size: Decimal


class SqlAlchemyInstrumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, instrument_id: str) -> InstrumentSnapshot | None:
        row = await self._session.get(InstrumentRow, instrument_id)
        if row is None:
            return None
        return InstrumentSnapshot(
            instrument_id=row.instrument_id,
            symbol=row.symbol,
            lot_size=row.lot_size,
            tick_size=row.tick_size,
        )
