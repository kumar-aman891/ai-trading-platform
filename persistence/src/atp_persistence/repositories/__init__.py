"""Repository implementations of the storage ports declared in
`atp_domain.ports.storage`: `TradeProposalRepository`, `RiskDecisionRepository`,
`OrderRepository` (Phase 1 Step 6), and `AuditEventRepository` (Phase 1
Step 7, read-only). `SqlAlchemyKillSwitchStateRepository`,
`SqlAlchemyUserRepository`, `SqlAlchemySessionRepository`, and
`SqlAlchemyAuditEventWriter` are read/write additions with no matching
domain Protocol - see each module's own docstring for why. No other entity
gets a repository class here (fills, positions, cash ledger, etc. are
ORM-modelled but have no domain-level repository protocol yet, so none is
fabricated for them).
"""

from __future__ import annotations

from atp_persistence.repositories.audit_events import SqlAlchemyAuditEventRepository
from atp_persistence.repositories.audit_writer import SqlAlchemyAuditEventWriter
from atp_persistence.repositories.kill_switches import (
    KillSwitchStateSnapshot,
    SqlAlchemyKillSwitchStateRepository,
)
from atp_persistence.repositories.orders import SqlAlchemyOrderRepository
from atp_persistence.repositories.risk_decisions import SqlAlchemyRiskDecisionRepository
from atp_persistence.repositories.sessions import SessionRecord, SqlAlchemySessionRepository
from atp_persistence.repositories.trade_proposals import SqlAlchemyTradeProposalRepository
from atp_persistence.repositories.users import SqlAlchemyUserRepository, UserRecord

__all__ = [
    "KillSwitchStateSnapshot",
    "SessionRecord",
    "SqlAlchemyAuditEventRepository",
    "SqlAlchemyAuditEventWriter",
    "SqlAlchemyKillSwitchStateRepository",
    "SqlAlchemyOrderRepository",
    "SqlAlchemyRiskDecisionRepository",
    "SqlAlchemySessionRepository",
    "SqlAlchemyTradeProposalRepository",
    "SqlAlchemyUserRepository",
    "UserRecord",
]
