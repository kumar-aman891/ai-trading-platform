"""Concrete implementation of `atp_domain.ports.storage.PositionRepository`.

`paper.positions` holds exactly one row per (mode, instrument_id)
(`uq_positions_mode_instrument`) - `upsert` updates that row in place when
it already exists, and inserts a new one only the first time a
mode/instrument pair is traded.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.orders import Position
from atp_domain.types import InstrumentId, Mode
from atp_persistence.mappers import position_to_row, row_to_position
from atp_persistence.models.paper import PositionRow


class SqlAlchemyPositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, mode: Mode, instrument_id: InstrumentId) -> Position | None:
        result = await self._session.execute(
            select(PositionRow).where(
                PositionRow.mode == mode.value, PositionRow.instrument_id == str(instrument_id)
            )
        )
        row = result.scalar_one_or_none()
        return row_to_position(row) if row is not None else None

    async def list_all(self, mode: Mode) -> Sequence[Position]:
        """Phase 1 Step 10's `GET /api/v1/paper/positions` ledger read.
        Not part of the `PositionRepository` Protocol - `atp_exec_paper`
        never needs to list every position, only look one up by
        instrument. Every `paper.positions` row is returned; a flat
        (`quantity == 0`) position stays a row (`position.md` has no
        delete-on-flat rule), so callers filter if they only want open
        exposure."""
        result = await self._session.execute(
            select(PositionRow)
            .where(PositionRow.mode == mode.value)
            .order_by(PositionRow.instrument_id)
        )
        return [row_to_position(row) for row in result.scalars().all()]

    async def upsert(self, position: Position) -> None:
        existing = await self._session.get(PositionRow, position.position_id)
        if existing is None:
            self._session.add(position_to_row(position))
        else:
            existing.quantity = position.quantity
            existing.average_price = (
                position.average_price.value if position.average_price is not None else None
            )
            existing.realized_pnl = position.realized_pnl.value
            existing.unrealized_pnl = position.unrealized_pnl.value
            existing.updated_at = position.updated_at
        await self._session.flush()
