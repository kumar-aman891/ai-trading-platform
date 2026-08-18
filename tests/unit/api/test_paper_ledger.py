"""`GET /api/v1/paper/{proposals,positions,cash}` (Phase 1 Step 10) -
exercised through real HTTP requests against the fully-wired app, database
dependencies swapped for in-memory fakes (`tests/unit/api/conftest.py`).
No Docker required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_PAPER_TRADER, ROLE_VIEWER
from atp_domain.ids import SequentialIdGenerator
from atp_domain.money import Money, Price, Quantity
from atp_domain.orders import Fill, Order, Position
from atp_domain.proposals import TradeProposal
from atp_domain.risk.engine import RiskDecision
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.types import (
    DecisionOutcome,
    FillId,
    InstrumentId,
    IntentId,
    Mode,
    OrderId,
    OrderStatus,
    OrderType,
    Product,
    ProposalId,
    RiskConfigId,
    Side,
)
from atp_persistence.repositories import UserRecord
from tests.unit.api.fakes import FakeUnitOfWork

_PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hash_password(_PASSWORD)  # module-level: argon2 hashing is slow, hash once
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_user(uow: FakeUnitOfWork, *, username: str, role: str) -> str:
    user_id = f"user-{username}"
    uow.users._by_id[user_id] = UserRecord(
        user_id=user_id,
        username=username,
        password_hash=_PASSWORD_HASH,
        role=role,
        is_active=True,
        must_change_password=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    return user_id


def _login(client: TestClient, *, username: str):
    return client.post("/api/v1/auth/login", json={"username": username, "password": _PASSWORD})


def _authenticated_trader(client: TestClient, fake_uow: FakeUnitOfWork, *, username: str = "t1"):
    _seed_user(fake_uow, username=username, role=ROLE_PAPER_TRADER)
    _login(client, username=username)


def _make_proposal(*, proposal_id: str, client_request_id: str) -> TradeProposal:
    return TradeProposal(
        proposal_id=ProposalId(proposal_id),
        mode=Mode.PAPER,
        instrument_id=InstrumentId("instr-1"),
        side=Side.BUY,
        quantity=Quantity(Decimal("10")),
        order_type=OrderType.LIMIT,
        limit_price=Price(Decimal("100")),
        trigger_price=None,
        product=Product.CNC,
        client_request_id=client_request_id,
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# GET /paper/proposals/{id}
# ---------------------------------------------------------------------------


def test_get_unknown_proposal_returns_404(client: TestClient, fake_uow) -> None:
    _authenticated_trader(client, fake_uow)
    response = client.get("/api/v1/paper/proposals/does-not-exist")
    assert response.status_code == 404


def test_get_proposal_with_no_decision_yet_has_null_decision_order_fill(
    client: TestClient, fake_uow
) -> None:
    proposal = _make_proposal(proposal_id="p-1", client_request_id="req-1")
    fake_uow.trade_proposals._by_id["p-1"] = proposal
    fake_uow.trade_proposals._proposal_id_by_client_request_id["req-1"] = "p-1"
    _authenticated_trader(client, fake_uow)

    response = client.get("/api/v1/paper/proposals/p-1")
    assert response.status_code == 200
    body = response.json()
    assert body["proposal_id"] == "p-1"
    assert body["quantity"] == "10"
    assert body["limit_price"] == "100"
    assert body["decision"] is None
    assert body["order"] is None
    assert body["fill"] is None


def test_get_proposal_nests_decision_order_and_fill_once_they_exist(
    client: TestClient,
    fake_uow,
    fake_risk_decision_repository,
    fake_order_repository,
    fake_fill_repository,
) -> None:
    proposal = _make_proposal(proposal_id="p-2", client_request_id="req-2")
    fake_uow.trade_proposals._by_id["p-2"] = proposal
    fake_uow.trade_proposals._proposal_id_by_client_request_id["req-2"] = "p-2"

    id_gen = SequentialIdGenerator()
    decision = RiskDecision(
        decision_id=id_gen.new_id(),
        mode=Mode.PAPER,
        proposal_id=ProposalId("p-2"),
        outcome=DecisionOutcome.APPROVED,
        rule_results=(RuleResult(rule_id="RISK.MODE.001", outcome=RuleOutcome.PASS, message="ok"),),
        risk_config_id=RiskConfigId(id_gen.new_id()),
        limit_snapshot_hash="hash",
        decided_at=_NOW,
    )
    fake_risk_decision_repository._by_proposal_id["p-2"] = decision

    order = Order(
        internal_order_id=OrderId(id_gen.new_id()),
        mode=Mode.PAPER,
        proposal_id=ProposalId("p-2"),
        intent_id=IntentId(id_gen.new_id()),
        idempotency_key="idem-1",
        status=OrderStatus.FILLED,
        submitted_at=_NOW,
        acknowledged_at=_NOW,
        last_update_at=_NOW,
    )
    fake_order_repository._by_proposal_id["p-2"] = order

    fill = Fill(
        fill_id=FillId(id_gen.new_id()),
        mode=Mode.PAPER,
        internal_order_id=order.internal_order_id,
        quantity=Quantity(Decimal("10")),
        price=Price(Decimal("100")),
        fees=Money(Decimal("0")),
        taxes=Money(Decimal("0")),
        simulated=True,
        filled_at=_NOW,
    )
    fake_fill_repository._fills.append(fill)

    _authenticated_trader(client, fake_uow)
    response = client.get("/api/v1/paper/proposals/p-2")
    assert response.status_code == 200
    body = response.json()
    assert body["decision"]["outcome"] == "APPROVED"
    assert body["decision"]["rule_results"][0]["rule_id"] == "RISK.MODE.001"
    assert body["order"]["status"] == "FILLED"
    assert body["fill"]["quantity"] == "10"
    assert body["fill"]["price"] == "100"
    assert body["fill"]["simulated"] is True


def test_get_proposal_requires_read_paper_ledger_permission(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="viewer1", role=ROLE_VIEWER)
    _login(client, username="viewer1")

    response = client.get("/api/v1/paper/proposals/anything")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /paper/proposals
# ---------------------------------------------------------------------------


def test_list_proposals_returns_newest_first(client: TestClient, fake_uow) -> None:
    older = _make_proposal(proposal_id="p-old", client_request_id="req-old")
    fake_uow.trade_proposals._by_id["p-old"] = older
    fake_uow.trade_proposals._proposal_id_by_client_request_id["req-old"] = "p-old"

    newer = TradeProposal(
        proposal_id=ProposalId("p-new"),
        mode=Mode.PAPER,
        instrument_id=InstrumentId("instr-1"),
        side=Side.BUY,
        quantity=Quantity(Decimal("5")),
        order_type=OrderType.LIMIT,
        limit_price=Price(Decimal("50")),
        trigger_price=None,
        product=Product.CNC,
        client_request_id="req-new",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    fake_uow.trade_proposals._by_id["p-new"] = newer
    fake_uow.trade_proposals._proposal_id_by_client_request_id["req-new"] = "p-new"

    _authenticated_trader(client, fake_uow)
    response = client.get("/api/v1/paper/proposals")
    assert response.status_code == 200
    ids = [item["proposal_id"] for item in response.json()["items"]]
    assert ids == ["p-new", "p-old"]


# ---------------------------------------------------------------------------
# GET /paper/positions
# ---------------------------------------------------------------------------


def test_list_positions_returns_decimal_faithful_values(
    client: TestClient, fake_uow, fake_position_repository
) -> None:
    fake_position_repository._positions.append(
        Position(
            position_id="pos-1",
            instrument_id=InstrumentId("instr-1"),
            mode=Mode.PAPER,
            quantity=Decimal("10"),
            average_price=Price(Decimal("123.456789")),
            realized_pnl=Money(Decimal("0")),
            unrealized_pnl=Money(Decimal("0")),
            updated_at=_NOW,
        )
    )
    _authenticated_trader(client, fake_uow)

    response = client.get("/api/v1/paper/positions")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == "10"
    assert items[0]["average_price"] == "123.456789"


def test_list_positions_requires_read_paper_ledger_permission(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="viewer2", role=ROLE_VIEWER)
    _login(client, username="viewer2")

    response = client.get("/api/v1/paper/positions")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /paper/cash
# ---------------------------------------------------------------------------


def test_get_cash_balance_returns_the_current_balance(
    client: TestClient, fake_uow, fake_cash_ledger_repository
) -> None:
    fake_cash_ledger_repository._balance = Decimal("10000000.000000")
    _authenticated_trader(client, fake_uow)

    response = client.get("/api/v1/paper/cash")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "PAPER"
    assert body["balance"] == "10000000.000000"


def test_get_cash_balance_is_null_when_no_ledger_entry_exists(client: TestClient, fake_uow) -> None:
    _authenticated_trader(client, fake_uow)
    response = client.get("/api/v1/paper/cash")
    assert response.status_code == 200
    assert response.json()["balance"] is None
