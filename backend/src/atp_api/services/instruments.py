"""`GET /api/v1/instruments` application logic - read-only (Phase 1 Step 10).

Lists every currently-active `core.instruments` row
(`SqlAlchemyInstrumentRepository.list_active`). No filtering/search is
implemented here - Phase 1 seeds only ~20 FIXTURE rows
(docs/schemas/instrument.md), so a full list is small enough to return
whole; a search/screener surface is a Phase 2+ data-plane concern
(docs/ROADMAP.md), not this milestone's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from atp_persistence.models.core import InstrumentRow
from atp_persistence.repositories import SqlAlchemyInstrumentRepository


@dataclass(frozen=True, slots=True)
class InstrumentView:
    instrument_id: str
    symbol: str
    name: str
    exchange: str
    segment: str
    lot_size: int
    tick_size: Decimal


def _to_view(row: InstrumentRow) -> InstrumentView:
    return InstrumentView(
        instrument_id=row.instrument_id,
        symbol=row.symbol,
        name=row.name,
        exchange=row.exchange,
        segment=row.segment,
        lot_size=row.lot_size,
        tick_size=row.tick_size,
    )


async def list_instruments(repository: SqlAlchemyInstrumentRepository) -> Sequence[InstrumentView]:
    rows = await repository.list_active()
    return [_to_view(row) for row in rows]
