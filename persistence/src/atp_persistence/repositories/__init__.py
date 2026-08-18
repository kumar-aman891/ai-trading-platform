"""Repository implementations of the storage ports declared in
`atp_domain.ports.storage`: `TradeProposalRepository`, `RiskDecisionRepository`,
`OrderRepository`. These three are the only repository interfaces the
domain declares as of Phase 1 Step 6 - no other entity gets a repository
class here (fills, positions, cash ledger, etc. are ORM-modelled but have
no domain-level repository protocol yet, so none is fabricated for them).
"""

from __future__ import annotations

from atp_persistence.repositories.orders import SqlAlchemyOrderRepository
from atp_persistence.repositories.risk_decisions import SqlAlchemyRiskDecisionRepository
from atp_persistence.repositories.trade_proposals import SqlAlchemyTradeProposalRepository

__all__ = [
    "SqlAlchemyOrderRepository",
    "SqlAlchemyRiskDecisionRepository",
    "SqlAlchemyTradeProposalRepository",
]
