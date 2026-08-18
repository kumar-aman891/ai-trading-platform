"""Concrete implementation of `atp_domain.ports.storage.FillRepository`.

`source` is passed alongside the domain `Fill` the same way
`atp_persistence.repositories.trade_proposals` takes `created_by` beside a
`TradeProposal` - `paper.fills.source` is provenance metadata
(`atp_persistence.mappers`'s module docstring), not a domain fact.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.orders import Fill
from atp_persistence.mappers import fill_to_row


class SqlAlchemyFillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, fill: Fill, *, source: str) -> None:
        self._session.add(fill_to_row(fill, source=source))
        await self._session.flush()
