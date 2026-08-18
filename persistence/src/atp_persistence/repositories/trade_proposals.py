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

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.proposals import TradeProposal
from atp_domain.types import Mode, ProposalId
from atp_persistence.mappers import row_to_trade_proposal, trade_proposal_to_row
from atp_persistence.models.paper import RiskDecisionRow, TradeProposalRow

_MAX_LIST_LIMIT = 200


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

    async def list_unevaluated_paper_proposal_ids(self, *, limit: int) -> Sequence[str]:
        """Not part of the Protocol; exposed for `atp_exec_paper`'s claim
        loop (ADR-011) - PAPER proposals with no matching
        `paper.risk_decisions` row, oldest first. A plain, unlocked SELECT:
        exclusivity for concurrent claimants is enforced by the
        `UNIQUE (proposal_id)` constraint on `paper.risk_decisions` at
        write time (ADR-011), not by a row lock here - `atp_paper_exec`
        holds only `SELECT` on `paper.trade_proposals` (migration 0003),
        which `SELECT ... FOR UPDATE` cannot run under in PostgreSQL."""
        result = await self._session.execute(
            select(TradeProposalRow.proposal_id)
            .outerjoin(RiskDecisionRow, RiskDecisionRow.proposal_id == TradeProposalRow.proposal_id)
            .where(
                TradeProposalRow.mode == Mode.PAPER.value,
                RiskDecisionRow.decision_id.is_(None),
            )
            .order_by(TradeProposalRow.created_at)
            .limit(limit)
        )
        return [row[0] for row in result.all()]

    async def list_for_mode(
        self, mode: Mode, *, before: datetime | None, limit: int
    ) -> Sequence[TradeProposal]:
        """Phase 1 Step 10's `GET /api/v1/paper/proposals` ledger read.
        Newest first (`created_at DESC`), keyset-paginated via `before` -
        mirrors `SqlAlchemyAuditEventRepository.list_recent`'s existing
        pattern rather than offset pagination, for the same reason: stable
        paging as new proposals are appended."""
        bounded_limit = max(1, min(limit, _MAX_LIST_LIMIT))
        stmt = (
            select(TradeProposalRow)
            .where(TradeProposalRow.mode == mode.value)
            .order_by(TradeProposalRow.created_at.desc(), TradeProposalRow.proposal_id.desc())
        )
        if before is not None:
            stmt = stmt.where(TradeProposalRow.created_at < before)
        stmt = stmt.limit(bounded_limit)

        result = await self._session.execute(stmt)
        return [row_to_trade_proposal(row) for row in result.scalars().all()]
