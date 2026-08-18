"""`atp_exec_paper.risk_runner` - RuleContext assembly from authoritative
sources only, and delegation to atp_domain.risk.engine.evaluate."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import MappingProxyType

import pytest

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_domain.types import DecisionOutcome, Mode
from atp_exec_paper.risk_runner import RiskConfigUnavailableError, evaluate_proposal
from tests.unit.exec_paper.builders import (
    make_config,
    make_instrument,
    make_paper_kill_switch_snapshot,
    make_proposal,
)
from tests.unit.exec_paper.fakes import FakeKillSwitchStateRepository, FakePaperExecutionUnitOfWork


def _clock() -> FrozenClock:
    from datetime import UTC, datetime

    return FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))


def test_expected_risk_is_never_read_by_the_risk_runner() -> None:
    """A proposal with a poisoned/adversarial `expected_risk` payload must
    evaluate identically to one with an empty payload - nothing in
    `risk_runner` ever inspects the field."""

    async def run() -> None:
        uow = FakePaperExecutionUnitOfWork()
        proposal = make_proposal()
        object.__setattr__(
            proposal,
            "expected_risk",
            MappingProxyType({"outcome": "APPROVED", "max_order_notional": "0"}),
        )
        uow.trade_proposals._by_id[proposal.proposal_id] = proposal
        uow.risk_config._active[Mode.PAPER] = make_config()
        instrument = make_instrument()
        uow.instruments._by_id[instrument.instrument_id] = instrument
        uow.kill_switches = FakeKillSwitchStateRepository(
            snapshots=[make_paper_kill_switch_snapshot(engaged=False)]
        )
        uow.cash_ledger._balance = Decimal("1000000")

        decision = await evaluate_proposal(
            uow, proposal, id_generator=SequentialIdGenerator(), clock=_clock()
        )
        assert decision.outcome is DecisionOutcome.APPROVED

    asyncio.run(run())


def test_missing_active_risk_config_raises_rather_than_guessing() -> None:
    async def run() -> None:
        uow = FakePaperExecutionUnitOfWork()
        proposal = make_proposal()
        uow.trade_proposals._by_id[proposal.proposal_id] = proposal

        with pytest.raises(RiskConfigUnavailableError):
            await evaluate_proposal(
                uow, proposal, id_generator=SequentialIdGenerator(), clock=_clock()
            )

    asyncio.run(run())


def test_missing_instrument_yields_indeterminate_lot_tick_rule() -> None:
    async def run() -> None:
        uow = FakePaperExecutionUnitOfWork()
        proposal = make_proposal()
        uow.trade_proposals._by_id[proposal.proposal_id] = proposal
        uow.risk_config._active[Mode.PAPER] = make_config()
        uow.kill_switches = FakeKillSwitchStateRepository(
            snapshots=[make_paper_kill_switch_snapshot(engaged=False)]
        )
        uow.cash_ledger._balance = Decimal("1000000")
        # No instrument registered - lot_size/tick_size stay None.

        decision = await evaluate_proposal(
            uow, proposal, id_generator=SequentialIdGenerator(), clock=_clock()
        )
        assert decision.outcome is DecisionOutcome.REJECTED
        lot_tick_result = next(r for r in decision.rule_results if r.rule_id == "RISK.ORDER.001")
        assert lot_tick_result.outcome.value == "INDETERMINATE"

    asyncio.run(run())
