"""Storage ports - one small repository protocol per Phase 1 entity.
Implementations land in atp_persistence (Phase 1 Step 6 for the first
three; Step 7 adds `AuditEventRepository`, read-only, for the audit
browsing API foundation; Step 9 adds `OrderIntentRepository`,
`FillRepository`, `PositionRepository` for the paper execution gateway).
Nothing here touches SQLAlchemy, a database, or any I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from atp_domain.audit import AuditEvent
from atp_domain.intents import ApprovedOrderIntent
from atp_domain.orders import Fill, Order, Position
from atp_domain.proposals import TradeProposal
from atp_domain.risk.engine import RiskDecision
from atp_domain.types import DecisionId, InstrumentId, Mode, OrderId, ProposalId


class TradeProposalRepository(Protocol):
    """`save` takes a required `created_by` keyword argument beyond the
    domain `TradeProposal` itself - `paper.trade_proposals.created_by` is
    NOT NULL (docs/schemas/trade_proposal.md) but the Step 4 domain
    dataclass has no field for it (ADR-009: it is provenance metadata, an
    application/auth-layer fact, not a business fact any risk rule reads).
    Corrected to match `SqlAlchemyTradeProposalRepository`'s actual,
    already-implemented signature now that Phase 1 Step 10 is this
    Protocol's first production caller (previously a documented Step 6 gap)."""

    async def save(self, proposal: TradeProposal, *, created_by: str) -> None: ...
    async def get(self, proposal_id: ProposalId) -> TradeProposal | None: ...


class RiskDecisionRepository(Protocol):
    async def save(self, decision: RiskDecision) -> None: ...
    async def get(self, decision_id: DecisionId) -> RiskDecision | None: ...
    async def get_by_proposal(self, proposal_id: ProposalId) -> RiskDecision | None: ...


class OrderRepository(Protocol):
    async def save(self, order: Order) -> None: ...
    async def get(self, order_id: OrderId) -> Order | None: ...
    async def get_by_proposal(self, proposal_id: ProposalId) -> Order | None: ...


class OrderIntentRepository(Protocol):
    """Persists a minted `ApprovedOrderIntent` (ADR-008). Deliberately no
    `get`/reconstruct method: `ApprovedOrderIntent` requires a genuine
    `MintingCapability` to construct (`atp_domain.intents`), which a
    repository reading a stored row can never hold - nothing in this
    codebase needs to reconstruct an intent as a live domain object after
    it has been written. `exists_for_decision` backs the single-use check
    a caller may want before minting; the database's own
    `UNIQUE (decision_id)` constraint is the actual enforcement mechanism,
    this is a convenience read, not a substitute for it."""

    async def save(self, intent: ApprovedOrderIntent) -> None: ...
    async def exists_for_decision(self, decision_id: DecisionId) -> bool: ...


class FillRepository(Protocol):
    """`source` is supplied alongside the domain `Fill` the same way
    `TradeProposalRepository.save` takes `created_by` beside a
    `TradeProposal` - `paper.fills.source` is provenance metadata
    (`atp_persistence.mappers`' module docstring), not a domain fact."""

    async def save(self, fill: Fill, *, source: str) -> None: ...


class PositionRepository(Protocol):
    """`paper.positions` holds exactly one row per (mode, instrument_id)
    (`uq_positions_mode_instrument`) - `upsert` updates that row in place
    once it exists, and creates it the first time a mode/instrument pair is
    traded."""

    async def get(self, mode: Mode, instrument_id: InstrumentId) -> Position | None: ...
    async def upsert(self, position: Position) -> None: ...


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
