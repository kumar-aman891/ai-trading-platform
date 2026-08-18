"""Concrete implementation of `atp_domain.ports.storage.TradeProposalRepository`.

`save()` takes an additional required `created_by` keyword argument beyond
the Protocol's bare `(self, proposal) -> None` signature - see the module
docstring in `atp_persistence.mappers` for why (`paper.trade_proposals.created_by`
is `NOT NULL` per docs/schemas/trade_proposal.md, but the Step 4 domain
`TradeProposal` dataclass has no field for it). This makes the class not a
strict structural match for the Protocol when called anonymously through
that type; documented as a known Step 6 gap rather than hidden behind a
fabricated default value.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.proposals import TradeProposal
from atp_domain.types import ProposalId
from atp_persistence.mappers import row_to_trade_proposal, trade_proposal_to_row
from atp_persistence.models.paper import TradeProposalRow


class SqlAlchemyTradeProposalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, proposal: TradeProposal, *, created_by: str) -> None:
        self._session.add(trade_proposal_to_row(proposal, created_by=created_by))
        await self._session.flush()

    async def get(self, proposal_id: ProposalId) -> TradeProposal | None:
        row = await self._session.get(TradeProposalRow, str(proposal_id))
        return row_to_trade_proposal(row) if row is not None else None

    async def get_by_client_request_id(self, client_request_id: str) -> TradeProposal | None:
        """Not part of the Protocol; exposed because
        `paper.trade_proposals.client_request_id` is the idempotency
        anchor documented in docs/schemas/order.md and callers need a way
        to detect a duplicate submission before minting anything."""
        result = await self._session.execute(
            select(TradeProposalRow).where(TradeProposalRow.client_request_id == client_request_id)
        )
        row = result.scalar_one_or_none()
        return row_to_trade_proposal(row) if row is not None else None
