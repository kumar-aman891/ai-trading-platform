"""Concrete implementation of `atp_domain.ports.storage.OrderRepository`.

Matches the Protocol's `save(self, order: Order) -> None` signature exactly
- `atp_domain.orders.Order` carries `intent_id` directly (Step 6
reconciliation: `paper.orders.intent_id` is the authoritative link in the
TradeProposal -> RiskDecision -> ApprovedOrderIntent -> Order chain,
ADR-008, so it belongs on the domain type, not as a bolt-on persistence
parameter).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.orders import Order
from atp_domain.types import OrderId, ProposalId
from atp_persistence.mappers import order_to_row, row_to_order
from atp_persistence.models.paper import OrderRow


class SqlAlchemyOrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, order: Order) -> None:
        self._session.add(order_to_row(order))
        await self._session.flush()

    async def get(self, order_id: OrderId) -> Order | None:
        row = await self._session.get(OrderRow, str(order_id))
        return row_to_order(row) if row is not None else None

    async def get_by_proposal(self, proposal_id: ProposalId) -> Order | None:
        result = await self._session.execute(
            select(OrderRow).where(OrderRow.proposal_id == str(proposal_id))
        )
        row = result.scalar_one_or_none()
        return row_to_order(row) if row is not None else None

    async def get_by_proposals(
        self, proposal_ids: Sequence[ProposalId]
    ) -> Mapping[ProposalId, Order]:
        """Batched form of `get_by_proposal` - one query for many proposals
        (`atp_api.services.paper_ledger.list_proposals`'s N+1 fix).
        `paper.orders.proposal_id` is unique (`OrderRow`), so at most one
        entry per key; a proposal_id with no order yet is simply absent
        from the returned mapping, matching `get_by_proposal`'s `None`."""
        if not proposal_ids:
            return {}
        result = await self._session.execute(
            select(OrderRow).where(OrderRow.proposal_id.in_([str(p) for p in proposal_ids]))
        )
        return {ProposalId(row.proposal_id): row_to_order(row) for row in result.scalars().all()}
