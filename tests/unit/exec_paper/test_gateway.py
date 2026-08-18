"""`atp_exec_paper.gateway` exercised end to end against in-memory fakes
(`tests/unit/exec_paper/fakes.py`) - no database required. Covers the
Step 9 safety invariants that do not themselves require real PostgreSQL
concurrency (that half lives in `tests/integration/db/`, Docker-gated).

Repository protocols declare `async def`, so - mirroring
`tests/integration/db/test_repositories.py`'s existing convention (this
workspace has no `pytest-asyncio` dependency) - every test here drives its
async body with `asyncio.run()` inside an ordinary sync test function.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_domain.risk.outcomes import RuleOutcome
from atp_domain.types import DecisionOutcome, Mode, OrderStatus, OrderType, Side
from atp_exec_paper.gateway import (
    CashLedgerUnavailableError,
    execute_proposal,
    idempotency_key,
)
from atp_exec_paper.risk_runner import RiskConfigUnavailableError
from tests.unit.exec_paper.builders import (
    make_config,
    make_instrument,
    make_paper_kill_switch_snapshot,
    make_proposal,
)
from tests.unit.exec_paper.fakes import (
    FakeKillSwitchStateRepository,
    FakePaperExecutionUnitOfWork,
    FakeRiskDecisionRepository,
    FakeTradeProposalRepository,
)

_PROPOSAL_ID = "22222222-2222-7222-8222-222222222222"


def _clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))


def _approvable_uow(**overrides: object) -> FakePaperExecutionUnitOfWork:
    uow = FakePaperExecutionUnitOfWork(**overrides)  # type: ignore[arg-type]
    uow.trade_proposals._by_id.setdefault(_PROPOSAL_ID, make_proposal())
    uow.risk_config._active[Mode.PAPER] = make_config()
    instrument = make_instrument()
    uow.instruments._by_id[instrument.instrument_id] = instrument
    uow.kill_switches = FakeKillSwitchStateRepository(
        snapshots=[make_paper_kill_switch_snapshot(engaged=False)]
    )
    uow.cash_ledger._balance = Decimal("1000000")
    return uow


def test_approved_limit_proposal_executes_the_full_pipeline() -> None:
    async def run() -> None:
        uow = _approvable_uow()
        outcome = await execute_proposal(
            uow, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
        )

        assert outcome.decision_outcome is DecisionOutcome.APPROVED
        assert outcome.order_id is not None
        assert outcome.already_claimed is False

        assert len(uow.order_intents.saved) == 1
        assert len(uow.orders.saved) == 1
        assert uow.orders.saved[0].status is OrderStatus.FILLED
        assert len(uow.fills.saved) == 1
        fill, source = uow.fills.saved[0]
        assert fill.simulated is True
        assert source == "PAPER_SIMULATOR"
        assert len(uow.cash_ledger.entries) == 1
        assert uow.cash_ledger.entries[0]["entry_type"] == "FILL_DEBIT"
        assert uow.cash_ledger._balance == Decimal("1000000") - Decimal("1000")
        assert len(uow.audit.events) >= 2

        assert len(uow.positions._by_key) == 1
        position = next(iter(uow.positions._by_key.values()))
        assert position.quantity == Decimal("10")
        assert position.average_price is not None
        assert position.average_price.value == Decimal("100")

    asyncio.run(run())


def test_market_proposal_is_rejected_and_produces_no_fill() -> None:
    async def run() -> None:
        uow = _approvable_uow()
        uow.trade_proposals._by_id[_PROPOSAL_ID] = make_proposal(
            order_type=OrderType.MARKET, limit_price=None
        )

        outcome = await execute_proposal(
            uow, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
        )

        assert outcome.decision_outcome is DecisionOutcome.REJECTED
        assert outcome.order_id is None
        assert uow.order_intents.saved == []
        assert uow.orders.saved == []
        assert uow.fills.saved == []
        assert uow.cash_ledger.entries == []
        decision = uow.risk_decisions.saved_by_proposal_id[_PROPOSAL_ID]
        data_result = next(r for r in decision.rule_results if r.rule_id == "RISK.DATA.001")
        assert data_result.outcome is RuleOutcome.INDETERMINATE

    asyncio.run(run())


def test_engaged_paper_kill_switch_rejects_with_zero_execution_rows() -> None:
    async def run() -> None:
        uow = _approvable_uow()
        uow.kill_switches = FakeKillSwitchStateRepository(
            snapshots=[make_paper_kill_switch_snapshot(engaged=True)]
        )

        outcome = await execute_proposal(
            uow, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
        )

        assert outcome.decision_outcome is DecisionOutcome.REJECTED
        assert uow.order_intents.saved == []
        assert uow.orders.saved == []
        assert uow.fills.saved == []
        assert uow.positions._by_key == {}
        assert uow.cash_ledger.entries == []

    asyncio.run(run())


def test_unreadable_kill_switch_state_rejects() -> None:
    async def run() -> None:
        uow = _approvable_uow()
        uow.kill_switches = FakeKillSwitchStateRepository(raise_on_read=True)

        outcome = await execute_proposal(
            uow, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
        )

        assert outcome.decision_outcome is DecisionOutcome.REJECTED
        assert uow.orders.saved == []

    asyncio.run(run())


def test_no_active_risk_config_fails_closed_by_raising() -> None:
    async def run() -> None:
        uow = _approvable_uow()
        uow.risk_config._active = {}

        with pytest.raises(RiskConfigUnavailableError):
            await execute_proposal(
                uow, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
            )
        assert uow.risk_decisions.saved_by_proposal_id == {}

    asyncio.run(run())


def test_cash_ledger_balance_vanishing_between_evaluation_and_execution_fails_closed() -> None:
    """A balance absent from the start already fails closed one layer
    earlier: `RISK.CAPITAL.001` (`SimulatedCashSufficiencyRule`) returns
    INDETERMINATE when `RuleContext.available_cash` is `None`, which the
    aggregator rejects before execution is ever attempted - covered by
    `test_insufficient_cash_rejects_with_zero_execution_rows`-adjacent
    behavior. This test instead exercises `_apply_position_and_cash`'s own
    defensive re-check: the balance was present during risk evaluation
    (letting the proposal reach APPROVED) but has vanished by the time the
    execution phase reads it again - a race this fake makes deterministic
    by returning a real balance once, then `None` on every subsequent
    call."""

    class _VanishingCashLedger:
        def __init__(self) -> None:
            self._calls = 0
            self.entries: list[dict[str, object]] = []

        async def get_balance(self, mode):  # type: ignore[no-untyped-def]
            self._calls += 1
            return Decimal("1000000") if self._calls == 1 else None

        async def append(self, **kwargs: object) -> None:  # pragma: no cover - unreachable
            self.entries.append(kwargs)

    async def run() -> None:
        uow = _approvable_uow()
        uow.cash_ledger = _VanishingCashLedger()  # type: ignore[assignment]

        with pytest.raises(CashLedgerUnavailableError):
            await execute_proposal(
                uow, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
            )

    asyncio.run(run())


def test_insufficient_cash_rejects_with_zero_execution_rows() -> None:
    async def run() -> None:
        uow = _approvable_uow()
        uow.cash_ledger._balance = Decimal("1")  # notional (1000) exceeds this

        outcome = await execute_proposal(
            uow, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
        )

        assert outcome.decision_outcome is DecisionOutcome.REJECTED
        assert uow.order_intents.saved == []
        assert uow.orders.saved == []
        assert uow.fills.saved == []
        assert uow.cash_ledger.entries == []

    asyncio.run(run())


def test_sell_side_credits_cash_ledger() -> None:
    async def run() -> None:
        uow = _approvable_uow()
        uow.trade_proposals._by_id[_PROPOSAL_ID] = make_proposal(side=Side.SELL)
        starting_balance = uow.cash_ledger._balance
        assert starting_balance is not None

        await execute_proposal(
            uow, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
        )

        assert uow.cash_ledger.entries[0]["entry_type"] == "FILL_CREDIT"
        assert uow.cash_ledger._balance == starting_balance + Decimal("1000")

    asyncio.run(run())


def test_concurrent_execution_of_the_same_proposal_produces_exactly_one_order() -> None:
    """Simulates two processes racing to claim the same proposal: both
    compute a decision independently (pure, no side effect until write
    time), but they share the same `risk_decisions`/`trade_proposals`
    stores - the second `risk_decisions.save()` raises `IntegrityError`
    (mirroring the real `UNIQUE (proposal_id)` constraint, ADR-011),
    stopping that claimant before it writes an order_intent/order/fill."""

    async def run() -> None:
        shared_proposals = FakeTradeProposalRepository([make_proposal()])
        shared_decisions = FakeRiskDecisionRepository()

        uow_a = _approvable_uow(trade_proposals=shared_proposals, risk_decisions=shared_decisions)
        uow_b = _approvable_uow(trade_proposals=shared_proposals, risk_decisions=shared_decisions)

        await execute_proposal(
            uow_a, _PROPOSAL_ID, id_generator=SequentialIdGenerator(), clock=_clock()
        )

        with pytest.raises(IntegrityError):
            await execute_proposal(
                uow_b,
                _PROPOSAL_ID,
                id_generator=SequentialIdGenerator(prefix="00000000-0000-7000-8001"),
                clock=_clock(),
            )

        total_orders = len(uow_a.orders.saved) + len(uow_b.orders.saved)
        total_intents = len(uow_a.order_intents.saved) + len(uow_b.order_intents.saved)
        assert total_orders == 1
        assert total_intents == 1

    asyncio.run(run())


def test_idempotency_key_matches_documented_derivation() -> None:
    proposal = make_proposal()
    assert proposal.limit_price is not None
    expected = hashlib.sha256(
        (
            "PAPER"
            + proposal.client_request_id
            + str(proposal.instrument_id)
            + proposal.side.value
            + str(proposal.quantity.value)
            + proposal.order_type.value
            + str(proposal.limit_price.value)
            + proposal.product.value
        ).encode("utf-8")
    ).hexdigest()

    assert idempotency_key(proposal, Mode.PAPER) == expected
