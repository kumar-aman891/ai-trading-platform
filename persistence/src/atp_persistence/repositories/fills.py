"""Concrete implementation of `atp_domain.ports.storage.FillRepository`.

`source` is passed alongside the domain `Fill` the same way
`atp_persistence.repositories.trade_proposals` takes `created_by` beside a
`TradeProposal` - `paper.fills.source` is provenance metadata
(`atp_persistence.mappers`'s module docstring), not a domain fact.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.orders import Fill
from atp_domain.types import OrderId
from atp_persistence.mappers import fill_to_row, row_to_fill
from atp_persistence.models.paper import FillRow


class SqlAlchemyFillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, fill: Fill, *, source: str) -> None:
        self._session.add(fill_to_row(fill, source=source))
        await self._session.flush()

    async def list_by_order(self, internal_order_id: OrderId) -> Sequence[Fill]:
        """Phase 1 Step 10's ledger read. Not part of the
        `FillRepository` Protocol - `atp_exec_paper` never needs to list
        fills back, only `atp_api`'s read path does. The Phase 1 simulator
        produces at most one fill per order (no partial fills - `fill.md`),
        so callers should expect a list of length 0 or 1, but this method
        does not itself assume or enforce that."""
        result = await self._session.execute(
            select(FillRow)
            .where(FillRow.internal_order_id == str(internal_order_id))
            .order_by(FillRow.filled_at)
        )
        return [row_to_fill(row) for row in result.scalars().all()]
