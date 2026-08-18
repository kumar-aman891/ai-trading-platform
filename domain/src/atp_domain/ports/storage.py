"""Storage ports - one small repository protocol per Phase 1 entity.
Implementations land in atp_persistence (Phase 1 Step 6 for the first
three; Step 7 adds `AuditEventRepository`, read-only, for the audit
browsing API foundation). Nothing here touches SQLAlchemy, a database, or
any I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from atp_domain.audit import AuditEvent
from atp_domain.orders import Order
from atp_domain.proposals import TradeProposal
from atp_domain.risk.engine import RiskDecision
from atp_domain.types import DecisionId, Mode, OrderId, ProposalId


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


class AuditEventRepository(Protocol):
    """Read-only by design in Phase 1 Step 7 - no route anywhere writes
    through this port. `audit.audit_events` is append-only at the database
    layer (grant + trigger, ADR-010); this Protocol simply never declares
    a `save`/`insert` method for API-layer callers to reach for."""

    async def list_recent(
        self,
        *,
        mode: Mode | None = None,
        action: str | None = None,
        before: datetime | None = None,
        limit: int = 50,
    ) -> Sequence[AuditEvent]:
        """Most recent events first (`occurred_at DESC`), optionally
        filtered to a `mode`/`action` and paged via a `before` cursor
        (strictly earlier than the given `occurred_at`) - keyset
        pagination over the `ix_audit_events_mode_action_occurred_at` /
        `ix_audit_events_occurred_at` indexes, not offset-based, so paging
        stays stable as new events are appended."""
        ...
