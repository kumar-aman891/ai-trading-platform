"""Concrete implementation of `atp_domain.ports.storage.RiskDecisionRepository`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.risk.engine import RiskDecision
from atp_domain.types import DecisionId, ProposalId
from atp_persistence.mappers import risk_decision_to_row, row_to_risk_decision
from atp_persistence.models.paper import RiskDecisionRow


class SqlAlchemyRiskDecisionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, decision: RiskDecision) -> None:
        self._session.add(risk_decision_to_row(decision))
        await self._session.flush()

    async def get(self, decision_id: DecisionId) -> RiskDecision | None:
        row = await self._session.get(RiskDecisionRow, str(decision_id))
        return row_to_risk_decision(row) if row is not None else None

    async def get_by_proposal(self, proposal_id: ProposalId) -> RiskDecision | None:
        result = await self._session.execute(
            select(RiskDecisionRow).where(RiskDecisionRow.proposal_id == str(proposal_id))
        )
        row = result.scalar_one_or_none()
        return row_to_risk_decision(row) if row is not None else None

    async def get_by_proposals(
        self, proposal_ids: Sequence[ProposalId]
    ) -> Mapping[ProposalId, RiskDecision]:
        """Batched form of `get_by_proposal` - one query for many proposals
        (`atp_api.services.paper_ledger.list_proposals`'s N+1 fix), rather
        than one query per proposal. `paper.risk_decisions.proposal_id` is
        unique (`RiskDecisionRow`), so at most one entry per key; a
        proposal_id with no decision yet is simply absent from the
        returned mapping, matching `get_by_proposal`'s `None` for that
        case."""
        if not proposal_ids:
            return {}
        result = await self._session.execute(
            select(RiskDecisionRow).where(
                RiskDecisionRow.proposal_id.in_([str(p) for p in proposal_ids])
            )
        )
        return {
            ProposalId(row.proposal_id): row_to_risk_decision(row) for row in result.scalars().all()
        }
