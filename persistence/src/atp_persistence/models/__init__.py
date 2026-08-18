"""ORM models, one module per PostgreSQL schema: core, audit, paper, live.

Every submodule is imported eagerly here so `Base.metadata` (and therefore
Alembic's `target_metadata`, and cross-schema `ForeignKey("schema.table.col")`
string references) sees every table regardless of which module a caller
imports directly. `live` defines no tables in Phase 1 - migration 0001
creates the schema only (ADR-005 §5.4).
"""

from __future__ import annotations

from atp_persistence.models import audit, core, live, paper
from atp_persistence.models.audit import AuditEventRow
from atp_persistence.models.base import Base
from atp_persistence.models.core import (
    InstrumentRow,
    JobQueueRow,
    KillSwitchHistoryRow,
    KillSwitchStateRow,
    RiskConfigRow,
    SessionRow,
    UserRow,
)
from atp_persistence.models.paper import (
    CashLedgerRow,
    FillRow,
    OrderIntentRow,
    OrderRow,
    PositionRow,
    RiskDecisionRow,
    TradeProposalRow,
)

__all__ = [
    "Base",
    "AuditEventRow",
    "InstrumentRow",
    "JobQueueRow",
    "KillSwitchHistoryRow",
    "KillSwitchStateRow",
    "RiskConfigRow",
    "SessionRow",
    "UserRow",
    "CashLedgerRow",
    "FillRow",
    "OrderIntentRow",
    "OrderRow",
    "PositionRow",
    "RiskDecisionRow",
    "TradeProposalRow",
    # Re-exported so `atp_persistence.models.<schema>` stays importable as a
    # namespace even though every class is also re-exported above.
    "audit",
    "core",
    "live",
    "paper",
]
