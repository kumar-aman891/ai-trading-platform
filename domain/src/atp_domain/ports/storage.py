"""Storage ports - one small repository protocol per Phase 1 entity.
Implementations land in atp_persistence at Phase 1 Step 8; nothing here
touches SQLAlchemy, a database, or any I/O.
"""

from __future__ import annotations

from typing import Protocol

from atp_domain.orders import Order
from atp_domain.proposals import TradeProposal
from atp_domain.risk.engine import RiskDecision
from atp_domain.types import DecisionId, OrderId, ProposalId


class TradeProposalRepository(Protocol):
    async def save(self, proposal: TradeProposal) -> None: ...
    async def get(self, proposal_id: ProposalId) -> TradeProposal | None: ...


class RiskDecisionRepository(Protocol):
    async def save(self, decision: RiskDecision) -> None: ...
    async def get(self, decision_id: DecisionId) -> RiskDecision | None: ...
    async def get_by_proposal(self, proposal_id: ProposalId) -> RiskDecision | None: ...


class OrderRepository(Protocol):
    async def save(self, order: Order) -> None: ...
    async def get(self, order_id: OrderId) -> Order | None: ...
    async def get_by_proposal(self, proposal_id: ProposalId) -> Order | None: ...
