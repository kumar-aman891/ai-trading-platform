"""In-memory fakes for the seven repositories `atp_exec_paper.uow.PaperExecutionUnitOfWork`
composes, duck-typed to the same method signatures as the real
SQLAlchemy-backed classes.

Used to exercise the full gateway logic (`atp_exec_paper.gateway`) without a
database - genuine claim-race/rejection/approval flows are otherwise
untestable without Docker (mirrors `tests/unit/api/fakes.py`'s precedent
and rationale). `FakeRiskDecisionRepository`/`FakeOrderIntentRepository`
simulate the real `UNIQUE (proposal_id)`/`UNIQUE (decision_id)` database
constraints by raising a real `sqlalchemy.exc.IntegrityError` on a
duplicate save, so `atp_exec_paper.gateway.run_once`'s claim-race handling
is exercised genuinely, not merely asserted about. Not a test file itself -
no `test_*` function lives here, so pytest does not collect it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from atp_domain.audit import AuditEvent
from atp_domain.intents import ApprovedOrderIntent
from atp_domain.orders import Fill, Order, Position
from atp_domain.proposals import TradeProposal
from atp_domain.risk.config import RiskConfig
from atp_domain.types import DecisionId, InstrumentId, Mode, OrderId, ProposalId
from atp_persistence.repositories import InstrumentSnapshot, KillSwitchStateSnapshot


class FakeTradeProposalRepository:
    def __init__(self, proposals: list[TradeProposal] | None = None) -> None:
        self._by_id: dict[str, TradeProposal] = {p.proposal_id: p for p in (proposals or [])}

    async def save(self, proposal: TradeProposal, *, created_by: str) -> None:
        self._by_id[proposal.proposal_id] = proposal

    async def get(self, proposal_id: ProposalId) -> TradeProposal | None:
        return self._by_id.get(proposal_id)

    async def get_by_client_request_id(self, client_request_id: str) -> TradeProposal | None:
        for proposal in self._by_id.values():
            if proposal.client_request_id == client_request_id:
                return proposal
        return None

    async def list_unevaluated_paper_proposal_ids(self, *, limit: int) -> list[str]:
        evaluated = getattr(self, "_evaluated_proposal_ids", set())
        candidates = [
            p.proposal_id
            for p in sorted(self._by_id.values(), key=lambda p: p.created_at)
            if p.mode is Mode.PAPER and p.proposal_id not in evaluated
        ]
        return candidates[:limit]


class FakeRiskDecisionRepository:
    def __init__(self) -> None:
        self.saved_by_proposal_id: dict[str, object] = {}

    async def save(self, decision) -> None:  # type: ignore[no-untyped-def]
        if decision.proposal_id in self.saved_by_proposal_id:
            raise IntegrityError(
                "INSERT INTO paper.risk_decisions ...",
                {},
                Exception("duplicate key value violates unique constraint"),
            )
        self.saved_by_proposal_id[decision.proposal_id] = decision

    async def get(self, decision_id):  # type: ignore[no-untyped-def]
        for decision in self.saved_by_proposal_id.values():
            if decision.decision_id == decision_id:
                return decision
        return None

    async def get_by_proposal(self, proposal_id: ProposalId):  # type: ignore[no-untyped-def]
        return self.saved_by_proposal_id.get(proposal_id)


class FakeOrderIntentRepository:
    def __init__(self) -> None:
        self.saved: list[ApprovedOrderIntent] = []

    async def save(self, intent: ApprovedOrderIntent) -> None:
        if any(existing.decision_id == intent.decision_id for existing in self.saved):
            raise IntegrityError(
                "INSERT INTO paper.order_intents ...",
                {},
                Exception("duplicate key value violates unique constraint"),
            )
        self.saved.append(intent)

    async def exists_for_decision(self, decision_id: DecisionId) -> bool:
        return any(existing.decision_id == decision_id for existing in self.saved)


class FakeOrderRepository:
    def __init__(self) -> None:
        self.saved: list[Order] = []

    async def save(self, order: Order) -> None:
        self.saved.append(order)

    async def get(self, order_id: OrderId) -> Order | None:
        for order in self.saved:
            if order.internal_order_id == order_id:
                return order
        return None

    async def get_by_proposal(self, proposal_id: ProposalId) -> Order | None:
        for order in self.saved:
            if order.proposal_id == proposal_id:
                return order
        return None


class FakeFillRepository:
    def __init__(self) -> None:
        self.saved: list[tuple[Fill, str]] = []

    async def save(self, fill: Fill, *, source: str) -> None:
        self.saved.append((fill, source))


class FakePositionRepository:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], Position] = {}

    async def get(self, mode: Mode, instrument_id: InstrumentId) -> Position | None:
        return self._by_key.get((mode.value, str(instrument_id)))

    async def upsert(self, position: Position) -> None:
        self._by_key[(position.mode.value, str(position.instrument_id))] = position


class FakeCashLedgerRepository:
    def __init__(self, *, opening_balance: Decimal | None = None) -> None:
        self.entries: list[dict[str, object]] = []
        self._balance: Decimal | None = opening_balance

    async def get_balance(self, mode: Mode) -> Decimal | None:
        return self._balance

    async def append(
        self,
        *,
        entry_id: str,
        mode: Mode,
        entry_type: str,
        amount: Decimal,
        related_fill_id: str | None,
        balance_after: Decimal,
        created_at: datetime,
    ) -> None:
        self.entries.append(
            {
                "entry_id": entry_id,
                "mode": mode.value,
                "entry_type": entry_type,
                "amount": amount,
                "related_fill_id": related_fill_id,
                "balance_after": balance_after,
                "created_at": created_at,
            }
        )
        self._balance = balance_after


class FakeInstrumentRepository:
    def __init__(self, instruments: list[InstrumentSnapshot] | None = None) -> None:
        self._by_id = {i.instrument_id: i for i in (instruments or [])}

    async def get(self, instrument_id: str) -> InstrumentSnapshot | None:
        return self._by_id.get(instrument_id)


class FakeRiskConfigRepository:
    def __init__(self, active_configs: dict[Mode, RiskConfig] | None = None) -> None:
        self._active = active_configs or {}

    async def get_active(self, mode: Mode) -> RiskConfig | None:
        return self._active.get(mode)


class FakeKillSwitchStateRepository:
    def __init__(
        self,
        snapshots: list[KillSwitchStateSnapshot] | None = None,
        *,
        raise_on_read: bool = False,
    ) -> None:
        self._snapshots = snapshots or []
        self._raise_on_read = raise_on_read

    async def list_all(self) -> list[KillSwitchStateSnapshot]:
        if self._raise_on_read:
            raise ConnectionError("simulated kill-switch read failure")
        return self._snapshots


class FakeAuditEventWriter:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def save(self, event: AuditEvent) -> None:
        self.events.append(event)


@dataclass
class FakePaperExecutionUnitOfWork:
    trade_proposals: FakeTradeProposalRepository = field(
        default_factory=FakeTradeProposalRepository
    )
    risk_decisions: FakeRiskDecisionRepository = field(default_factory=FakeRiskDecisionRepository)
    order_intents: FakeOrderIntentRepository = field(default_factory=FakeOrderIntentRepository)
    orders: FakeOrderRepository = field(default_factory=FakeOrderRepository)
    fills: FakeFillRepository = field(default_factory=FakeFillRepository)
    positions: FakePositionRepository = field(default_factory=FakePositionRepository)
    cash_ledger: FakeCashLedgerRepository = field(default_factory=FakeCashLedgerRepository)
    instruments: FakeInstrumentRepository = field(default_factory=FakeInstrumentRepository)
    risk_config: FakeRiskConfigRepository = field(default_factory=FakeRiskConfigRepository)
    kill_switches: FakeKillSwitchStateRepository = field(
        default_factory=FakeKillSwitchStateRepository
    )
    audit: FakeAuditEventWriter = field(default_factory=FakeAuditEventWriter)
    committed: bool = False
    rolled_back: bool = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
