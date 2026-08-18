"""In-memory fakes for `atp_persistence.repositories.users`/`sessions`/
`audit_writer`, duck-typed to the same method signatures as the real
SQLAlchemy-backed classes.

Used only to exercise `atp_api`'s authentication/RBAC *logic* (login,
logout, session validation, permission checks, audit-on-denial) through
real HTTP requests (`fastapi.testclient.TestClient` + FastAPI's
`dependency_overrides`) without a database - genuinely successful/expired/
revoked/CSRF-mismatched session flows are otherwise untestable without
Docker (see `tests/integration/db/`'s existing skip-gated convention,
which still owns the "does this actually round-trip through real
PostgreSQL/Alembic-migrated tables" question). Not a test file itself - no
`test_*` function lives here, so pytest does not collect it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from atp_domain.audit import AuditEvent
from atp_domain.orders import Fill, Order, Position
from atp_domain.proposals import TradeProposal
from atp_domain.risk.engine import RiskDecision
from atp_domain.types import Mode, OrderId, ProposalId
from atp_persistence.repositories import SessionRecord, UserRecord


class FakeUserRepository:
    def __init__(self, users: list[UserRecord] | None = None) -> None:
        self._by_id: dict[str, UserRecord] = {u.user_id: u for u in (users or [])}

    async def get_by_username(self, username: str) -> UserRecord | None:
        lowered = username.lower()
        for user in self._by_id.values():
            if user.username.lower() == lowered:
                return user
        return None

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        return self._by_id.get(user_id)

    async def count(self) -> int:
        return len(self._by_id)

    async def create(
        self,
        *,
        user_id: str,
        username: str,
        password_hash: str,
        role: str,
        must_change_password: bool,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self._by_id[user_id] = UserRecord(
            user_id=user_id,
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=True,
            must_change_password=must_change_password,
            created_at=created_at,
            updated_at=updated_at,
        )


class FakeSessionRepository:
    def __init__(self) -> None:
        self._by_hash: dict[str, SessionRecord] = {}

    async def get_by_hash(self, session_id_hash: str) -> SessionRecord | None:
        return self._by_hash.get(session_id_hash)

    async def create(
        self,
        *,
        session_id_hash: str,
        user_id: str,
        csrf_token: str,
        created_at: datetime,
        expires_at: datetime,
        ip_address: str | None,
    ) -> None:
        self._by_hash[session_id_hash] = SessionRecord(
            session_id_hash=session_id_hash,
            user_id=user_id,
            csrf_token=csrf_token,
            created_at=created_at,
            expires_at=expires_at,
            revoked_at=None,
            ip_address=ip_address,
        )

    async def extend_expiry(self, session_id_hash: str, *, new_expires_at: datetime) -> None:
        existing = self._by_hash.get(session_id_hash)
        if existing is not None and existing.revoked_at is None:
            self._by_hash[session_id_hash] = SessionRecord(
                session_id_hash=existing.session_id_hash,
                user_id=existing.user_id,
                csrf_token=existing.csrf_token,
                created_at=existing.created_at,
                expires_at=new_expires_at,
                revoked_at=None,
                ip_address=existing.ip_address,
            )

    async def revoke(self, session_id_hash: str, *, revoked_at: datetime) -> None:
        existing = self._by_hash.get(session_id_hash)
        if existing is not None and existing.revoked_at is None:
            self._by_hash[session_id_hash] = SessionRecord(
                session_id_hash=existing.session_id_hash,
                user_id=existing.user_id,
                csrf_token=existing.csrf_token,
                created_at=existing.created_at,
                expires_at=existing.expires_at,
                revoked_at=revoked_at,
                ip_address=existing.ip_address,
            )


class FakeAuditEventWriter:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def save(self, event: AuditEvent) -> None:
        self.events.append(event)


class FakeTradeProposalRepository:
    """Phase 1 Step 10. Raises a real `sqlalchemy.exc.IntegrityError` on a
    duplicate `client_request_id` (mirroring `paper.trade_proposals`'
    `UNIQUE (client_request_id)` constraint), matching the same simulated-
    constraint pattern `tests/unit/exec_paper/fakes.py` already uses for
    `paper.risk_decisions`' `UNIQUE (proposal_id)` - so
    `atp_api.services.paper_proposals.submit_proposal`'s claim-race/replay
    handling is exercised genuinely, not merely asserted about."""

    def __init__(self, proposals: list[TradeProposal] | None = None) -> None:
        self._by_id: dict[str, TradeProposal] = {p.proposal_id: p for p in (proposals or [])}
        self._proposal_id_by_client_request_id: dict[str, str] = {
            p.client_request_id: p.proposal_id for p in (proposals or [])
        }

    async def save(self, proposal: TradeProposal, *, created_by: str) -> None:
        if proposal.client_request_id in self._proposal_id_by_client_request_id:
            raise IntegrityError(
                "INSERT INTO paper.trade_proposals ...",
                {},
                Exception("duplicate key value violates unique constraint"),
            )
        self._by_id[proposal.proposal_id] = proposal
        self._proposal_id_by_client_request_id[proposal.client_request_id] = proposal.proposal_id

    async def get(self, proposal_id: ProposalId) -> TradeProposal | None:
        return self._by_id.get(proposal_id)

    async def get_by_client_request_id(self, client_request_id: str) -> TradeProposal | None:
        proposal_id = self._proposal_id_by_client_request_id.get(client_request_id)
        return self._by_id.get(proposal_id) if proposal_id is not None else None

    async def list_for_mode(
        self, mode: Mode, *, before: datetime | None, limit: int
    ) -> Sequence[TradeProposal]:
        items = sorted(
            (p for p in self._by_id.values() if p.mode is mode),
            key=lambda p: p.created_at,
            reverse=True,
        )
        if before is not None:
            items = [p for p in items if p.created_at < before]
        return items[:limit]


class FakeRiskDecisionRepository:
    def __init__(self, decisions: list[RiskDecision] | None = None) -> None:
        self._by_proposal_id: dict[str, RiskDecision] = {
            d.proposal_id: d for d in (decisions or [])
        }

    async def get_by_proposal(self, proposal_id: ProposalId) -> RiskDecision | None:
        return self._by_proposal_id.get(proposal_id)


class FakeOrderRepository:
    def __init__(self, orders: list[Order] | None = None) -> None:
        self._by_proposal_id: dict[str, Order] = {o.proposal_id: o for o in (orders or [])}

    async def get_by_proposal(self, proposal_id: ProposalId) -> Order | None:
        return self._by_proposal_id.get(proposal_id)


class FakeFillRepository:
    def __init__(self, fills: list[Fill] | None = None) -> None:
        self._fills: list[Fill] = list(fills or [])

    async def list_by_order(self, internal_order_id: OrderId) -> Sequence[Fill]:
        return [f for f in self._fills if f.internal_order_id == internal_order_id]


class FakePositionRepository:
    def __init__(self, positions: list[Position] | None = None) -> None:
        self._positions: list[Position] = list(positions or [])

    async def list_all(self, mode: Mode) -> Sequence[Position]:
        return [p for p in self._positions if p.mode is mode]


class FakeCashLedgerRepository:
    def __init__(self, *, balance: Decimal | None = None) -> None:
        self._balance = balance

    async def get_balance(self, mode: Mode) -> Decimal | None:
        return self._balance


@dataclass(frozen=True, slots=True)
class FakeInstrumentRow:
    """Duck-typed to the columns `atp_api.services.instruments` reads off
    `atp_persistence.models.core.InstrumentRow` - not the ORM class itself,
    so this fixture needs no database."""

    instrument_id: str
    symbol: str
    name: str
    exchange: str
    segment: str
    lot_size: int
    tick_size: Decimal
    active_to: datetime | None = None


class FakeInstrumentRepository:
    def __init__(self, instruments: list[FakeInstrumentRow] | None = None) -> None:
        self._by_id: dict[str, FakeInstrumentRow] = {
            i.instrument_id: i for i in (instruments or [])
        }

    async def get(self, instrument_id: str) -> FakeInstrumentRow | None:
        return self._by_id.get(instrument_id)

    async def list_active(self) -> Sequence[FakeInstrumentRow]:
        return [i for i in self._by_id.values() if i.active_to is None]


@dataclass
class FakeUnitOfWork:
    users: FakeUserRepository = field(default_factory=FakeUserRepository)
    sessions: FakeSessionRepository = field(default_factory=FakeSessionRepository)
    trade_proposals: FakeTradeProposalRepository = field(
        default_factory=FakeTradeProposalRepository
    )
    audit: FakeAuditEventWriter = field(default_factory=FakeAuditEventWriter)
    committed: bool = False
    rolled_back: bool = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
