"""Concrete implementation of `atp_domain.ports.storage.RiskDecisionRepository`."""

from __future__ import annotations

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
