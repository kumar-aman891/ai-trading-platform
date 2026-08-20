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
# GET /paper/proposals - batched N+1 fix (Phase 1 Step 17)
# ---------------------------------------------------------------------------


def _seed_full_chain(
    fake_uow,
    fake_risk_decision_repository,
    fake_order_repository,
    fake_fill_repository,
    *,
    suffix: str,
) -> str:
    """A proposal with a decision, an order, and a fill - the fully
    populated case. IDs are derived from `suffix`, not a fresh
    `SequentialIdGenerator` per helper - two helpers each starting their
    own generator at 1 would mint colliding order/decision/fill IDs across
    different proposals in the same test, corrupting `FakeFillRepository`'s
    per-order grouping."""
    proposal = _make_proposal(
        proposal_id=f"p-full-{suffix}", client_request_id=f"req-full-{suffix}"
    )
    fake_uow.trade_proposals._by_id[proposal.proposal_id] = proposal
    fake_uow.trade_proposals._proposal_id_by_client_request_id[proposal.client_request_id] = (
        proposal.proposal_id
    )
    decision = RiskDecision(
        decision_id=f"decision-full-{suffix}",
        mode=Mode.PAPER,
        proposal_id=proposal.proposal_id,
        outcome=DecisionOutcome.APPROVED,
        rule_results=(RuleResult(rule_id="RISK.MODE.001", outcome=RuleOutcome.PASS, message="ok"),),
        risk_config_id=RiskConfigId(f"risk-config-full-{suffix}"),
        limit_snapshot_hash="hash",
        decided_at=_NOW,
    )
    fake_risk_decision_repository._by_proposal_id[proposal.proposal_id] = decision
    order = Order(
        internal_order_id=OrderId(f"order-full-{suffix}"),
        mode=Mode.PAPER,
        proposal_id=proposal.proposal_id,
        intent_id=IntentId(f"intent-full-{suffix}"),
        idempotency_key=f"idem-{suffix}",
        status=OrderStatus.FILLED,
        submitted_at=_NOW,
        acknowledged_at=_NOW,
        last_update_at=_NOW,
    )
    fake_order_repository._by_proposal_id[proposal.proposal_id] = order
    fill = Fill(
        fill_id=FillId(f"fill-full-{suffix}"),
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
    return proposal.proposal_id


def _seed_bare_proposal(fake_uow, *, suffix: str, created_at: datetime) -> str:
    """A proposal with no decision, order, or fill yet."""
    proposal = TradeProposal(
        proposal_id=ProposalId(f"p-bare-{suffix}"),
        mode=Mode.PAPER,
        instrument_id=InstrumentId("instr-1"),
        side=Side.BUY,
        quantity=Quantity(Decimal("1")),
        order_type=OrderType.LIMIT,
        limit_price=Price(Decimal("1")),
        trigger_price=None,
        product=Product.CNC,
        client_request_id=f"req-bare-{suffix}",
        created_at=created_at,
    )
    fake_uow.trade_proposals._by_id[proposal.proposal_id] = proposal
    fake_uow.trade_proposals._proposal_id_by_client_request_id[proposal.client_request_id] = (
        proposal.proposal_id
    )
    return proposal.proposal_id


def _seed_decision_only(fake_uow, fake_risk_decision_repository, *, suffix: str) -> str:
    """A proposal with a decision but no order yet (rejected, or not
    reached the execution gateway)."""
    proposal = _make_proposal(
        proposal_id=f"p-decision-only-{suffix}", client_request_id=f"req-decision-only-{suffix}"
    )
    fake_uow.trade_proposals._by_id[proposal.proposal_id] = proposal
    fake_uow.trade_proposals._proposal_id_by_client_request_id[proposal.client_request_id] = (
        proposal.proposal_id
    )
    decision = RiskDecision(
        decision_id=f"decision-only-{suffix}",
        mode=Mode.PAPER,
        proposal_id=proposal.proposal_id,
        outcome=DecisionOutcome.REJECTED,
        rule_results=(
            RuleResult(rule_id="RISK.MODE.001", outcome=RuleOutcome.REJECT, message="rejected"),
        ),
        risk_config_id=RiskConfigId(f"risk-config-decision-only-{suffix}"),
        limit_snapshot_hash="hash",
        decided_at=_NOW,
    )
    fake_risk_decision_repository._by_proposal_id[proposal.proposal_id] = decision
    return proposal.proposal_id


def _seed_order_no_fill(
    fake_uow, fake_risk_decision_repository, fake_order_repository, *, suffix: str
) -> str:
    """A proposal with a decision and an order, but no fill yet (submitted,
    not yet executed)."""
    proposal = _make_proposal(
        proposal_id=f"p-order-only-{suffix}", client_request_id=f"req-order-only-{suffix}"
    )
    fake_uow.trade_proposals._by_id[proposal.proposal_id] = proposal
    fake_uow.trade_proposals._proposal_id_by_client_request_id[proposal.client_request_id] = (
        proposal.proposal_id
    )
    decision = RiskDecision(
        decision_id=f"decision-order-only-{suffix}",
        mode=Mode.PAPER,
        proposal_id=proposal.proposal_id,
        outcome=DecisionOutcome.APPROVED,
        rule_results=(RuleResult(rule_id="RISK.MODE.001", outcome=RuleOutcome.PASS, message="ok"),),
        risk_config_id=RiskConfigId(f"risk-config-order-only-{suffix}"),
        limit_snapshot_hash="hash",
        decided_at=_NOW,
    )
    fake_risk_decision_repository._by_proposal_id[proposal.proposal_id] = decision
    order = Order(
        internal_order_id=OrderId(f"order-order-only-{suffix}"),
        mode=Mode.PAPER,
        proposal_id=proposal.proposal_id,
        intent_id=IntentId(f"intent-order-only-{suffix}"),
        idempotency_key=f"idem-order-only-{suffix}",
        status=OrderStatus.SUBMITTED,
        submitted_at=_NOW,
        acknowledged_at=_NOW,
        last_update_at=_NOW,
    )
    fake_order_repository._by_proposal_id[proposal.proposal_id] = order
    return proposal.proposal_id


def test_list_proposals_mixed_page_matches_single_row_semantics(
    client: TestClient,
    fake_uow,
    fake_risk_decision_repository,
    fake_order_repository,
    fake_fill_repository,
) -> None:
    """One page containing all four states the old per-proposal path
    handled: no decision at all, decision but no order, order but no
    fill, and the fully populated order+fill case - proving the batched
    assembly (`_assemble_proposal_view` fed from maps) produces the exact
    same nesting the old single-row `_build_proposal_view` loop did."""
    full_id = _seed_full_chain(
        fake_uow,
        fake_risk_decision_repository,
        fake_order_repository,
        fake_fill_repository,
        suffix="1",
    )
    order_only_id = _seed_order_no_fill(
        fake_uow, fake_risk_decision_repository, fake_order_repository, suffix="1"
    )
    decision_only_id = _seed_decision_only(fake_uow, fake_risk_decision_repository, suffix="1")
    bare_id = _seed_bare_proposal(fake_uow, suffix="1", created_at=_NOW)

    _authenticated_trader(client, fake_uow)
    response = client.get("/api/v1/paper/proposals")
    assert response.status_code == 200
    by_id = {item["proposal_id"]: item for item in response.json()["items"]}

    assert by_id[bare_id]["decision"] is None
    assert by_id[bare_id]["order"] is None
    assert by_id[bare_id]["fill"] is None

    assert by_id[decision_only_id]["decision"]["outcome"] == "REJECTED"
    assert by_id[decision_only_id]["order"] is None
    assert by_id[decision_only_id]["fill"] is None

    assert by_id[order_only_id]["decision"]["outcome"] == "APPROVED"
    assert by_id[order_only_id]["order"]["status"] == "SUBMITTED"
    assert by_id[order_only_id]["fill"] is None

    assert by_id[full_id]["decision"]["outcome"] == "APPROVED"
    assert by_id[full_id]["order"]["status"] == "FILLED"
    assert by_id[full_id]["fill"]["quantity"] == "10"
    assert by_id[full_id]["fill"]["simulated"] is True


def test_list_proposals_issues_exactly_one_batched_call_per_data_type_and_no_per_proposal_calls(
    client: TestClient,
    fake_uow,
    fake_risk_decision_repository,
    fake_order_repository,
    fake_fill_repository,
) -> None:
    _seed_full_chain(
        fake_uow,
        fake_risk_decision_repository,
        fake_order_repository,
        fake_fill_repository,
        suffix="2",
    )
    _seed_order_no_fill(fake_uow, fake_risk_decision_repository, fake_order_repository, suffix="2")
    _seed_decision_only(fake_uow, fake_risk_decision_repository, suffix="2")
    _seed_bare_proposal(fake_uow, suffix="2", created_at=_NOW)

    _authenticated_trader(client, fake_uow)
    response = client.get("/api/v1/paper/proposals")
    assert response.status_code == 200

    assert len(fake_risk_decision_repository.get_by_proposals_calls) == 1
    assert fake_risk_decision_repository.get_by_proposal_calls == []
    assert len(fake_order_repository.get_by_proposals_calls) == 1
    assert fake_order_repository.get_by_proposal_calls == []
    assert len(fake_fill_repository.list_by_orders_calls) == 1
    assert fake_fill_repository.list_by_order_calls == []


def test_list_proposals_on_an_empty_page_makes_no_batched_calls(
    client: TestClient,
    fake_uow,
    fake_risk_decision_repository,
    fake_order_repository,
    fake_fill_repository,
) -> None:
    _authenticated_trader(client, fake_uow)
    response = client.get("/api/v1/paper/proposals")
    assert response.status_code == 200
    assert response.json()["items"] == []

    assert fake_risk_decision_repository.get_by_proposals_calls == []
    assert fake_order_repository.get_by_proposals_calls == []
    assert fake_fill_repository.list_by_orders_calls == []


def test_get_proposal_detail_still_uses_single_row_repository_methods(
    client: TestClient,
    fake_uow,
    fake_risk_decision_repository,
    fake_order_repository,
    fake_fill_repository,
) -> None:
    """`get_proposal_detail` (the single-proposal route) must remain on
    the O(1) single-row methods - it is explicitly out of scope for the
    batched-read fix."""
    proposal_id = _seed_full_chain(
        fake_uow,
        fake_risk_decision_repository,
        fake_order_repository,
        fake_fill_repository,
        suffix="3",
    )
    _authenticated_trader(client, fake_uow)

    response = client.get(f"/api/v1/paper/proposals/{proposal_id}")
    assert response.status_code == 200

    assert fake_risk_decision_repository.get_by_proposal_calls == [proposal_id]
    assert fake_risk_decision_repository.get_by_proposals_calls == []
    assert fake_order_repository.get_by_proposal_calls == [ProposalId(proposal_id)]
    assert fake_order_repository.get_by_proposals_calls == []
    assert len(fake_fill_repository.list_by_order_calls) == 1
    assert fake_fill_repository.list_by_orders_calls == []


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
