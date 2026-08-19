"""Repository implementations of the storage ports declared in
`atp_domain.ports.storage`: `TradeProposalRepository`, `RiskDecisionRepository`,
`OrderRepository` (Phase 1 Step 6), `AuditEventRepository` (Phase 1
Step 7, read-only), and `OrderIntentRepository`/`FillRepository`/
`PositionRepository` (Phase 1 Step 9, the paper execution gateway).
`SqlAlchemyKillSwitchStateRepository`, `SqlAlchemyUserRepository`,
`SqlAlchemySessionRepository`, `SqlAlchemyAuditEventWriter`,
`SqlAlchemyCashLedgerRepository`, `SqlAlchemyInstrumentRepository`,
`SqlAlchemyRiskConfigRepository`, `SqlAlchemyJobQueueRepository`, and
`SqlAlchemyWorkerSessionObservationRepository` are read/write additions
with no matching domain Protocol - see each module's own docstring for
why.
"""

from __future__ import annotations

from atp_persistence.repositories.audit_events import (
    SqlAlchemyAuditEventRepository,
    WindowAttestationStats,
)
from atp_persistence.repositories.audit_writer import SqlAlchemyAuditEventWriter
from atp_persistence.repositories.cash_ledger import SqlAlchemyCashLedgerRepository
from atp_persistence.repositories.fills import SqlAlchemyFillRepository
from atp_persistence.repositories.instruments import (
    InstrumentSnapshot,
    SqlAlchemyInstrumentRepository,
)
from atp_persistence.repositories.jobs import (
    ClaimedJob,
    ExpiredLease,
    SqlAlchemyJobQueueRepository,
)
from atp_persistence.repositories.kill_switches import (
    KillSwitchStateSnapshot,
    SqlAlchemyKillSwitchStateRepository,
)
from atp_persistence.repositories.order_intents import SqlAlchemyOrderIntentRepository
from atp_persistence.repositories.orders import SqlAlchemyOrderRepository
from atp_persistence.repositories.positions import SqlAlchemyPositionRepository
from atp_persistence.repositories.risk_config import SqlAlchemyRiskConfigRepository
from atp_persistence.repositories.risk_decisions import SqlAlchemyRiskDecisionRepository
from atp_persistence.repositories.session_observations import (
    SessionExpiryObservation,
    SqlAlchemyWorkerSessionObservationRepository,
)
from atp_persistence.repositories.sessions import SessionRecord, SqlAlchemySessionRepository
from atp_persistence.repositories.trade_proposals import SqlAlchemyTradeProposalRepository
from atp_persistence.repositories.users import SqlAlchemyUserRepository, UserRecord

__all__ = [
    "ClaimedJob",
    "ExpiredLease",
    "InstrumentSnapshot",
    "KillSwitchStateSnapshot",
    "SessionExpiryObservation",
    "SessionRecord",
    "SqlAlchemyAuditEventRepository",
    "SqlAlchemyAuditEventWriter",
    "SqlAlchemyCashLedgerRepository",
    "SqlAlchemyFillRepository",
    "SqlAlchemyInstrumentRepository",
    "SqlAlchemyJobQueueRepository",
    "SqlAlchemyKillSwitchStateRepository",
    "SqlAlchemyOrderIntentRepository",
    "SqlAlchemyOrderRepository",
    "SqlAlchemyPositionRepository",
    "SqlAlchemyRiskConfigRepository",
    "SqlAlchemyRiskDecisionRepository",
    "SqlAlchemySessionRepository",
    "SqlAlchemyTradeProposalRepository",
    "SqlAlchemyUserRepository",
    "SqlAlchemyWorkerSessionObservationRepository",
    "UserRecord",
    "WindowAttestationStats",
]
