"""`atp_strategy.runner` - strategy isolation, kill-switch consultation,
transaction choreography, and the poll loop (ADR-014, ADR-015, Milestone
2C). No database connection is used - every transaction boundary is
exercised against `RecordingUnitOfWorkFactory`."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_domain.money import Quantity
from atp_domain.strategy import ProposedTrade, StrategyRegistry
from atp_domain.types import InstrumentId, OrderType, Product, Side
from atp_persistence.repositories import KillSwitchStateSnapshot
from atp_strategy.runner import run_once, run_poll_cycle, run_poll_loop
from tests.unit.strategy.fakes import (
    FakeInstrumentRepository,
    FakeInstrumentRow,
    FakeKillSwitchStateRepository,
    RecordingStrategy,
    RecordingUnitOfWorkFactory,
)

_AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
_INSTRUMENT_ID = InstrumentId("11111111-1111-7111-8111-111111111111")
_INSTRUMENT_ROW = FakeInstrumentRow(str(_INSTRUMENT_ID), "FIXTURE", 1, Decimal("0.05"))


def _proposed_trade() -> ProposedTrade:
    return ProposedTrade(
        instrument_id=_INSTRUMENT_ID,
        side=Side.BUY,
        quantity=Quantity(Decimal(1)),
        order_type=OrderType.MARKET,
        limit_price=None,
        product=Product.CNC,
    )


def _strategy_switch_snapshot(strategy_key: str, *, engaged: bool) -> KillSwitchStateSnapshot:
    return KillSwitchStateSnapshot(
        switch_id=f"STRATEGY:{strategy_key}",
        engaged=engaged,
        updated_at=_AS_OF,
        updated_by=None,
        reason="test" if engaged else None,
    )


def _disengaged(strategy_key: str = "fake-strategy") -> KillSwitchStateSnapshot:
    return _strategy_switch_snapshot(strategy_key, engaged=False)


def _instruments(*, raise_on_read: bool = False) -> FakeInstrumentRepository:
    repo = FakeInstrumentRepository([_INSTRUMENT_ROW])
    if raise_on_read:

        async def _raise() -> list[FakeInstrumentRow]:
            raise ConnectionError("simulated instrument read failure")

        repo.list_active = _raise  # type: ignore[method-assign]
    return repo


def _kill_switches(
    snapshots: list[KillSwitchStateSnapshot], *, raise_on_read: bool = False
) -> FakeKillSwitchStateRepository:
    return FakeKillSwitchStateRepository(snapshots, raise_on_read=raise_on_read)  # type: ignore[arg-type]


# --- Kill-switch gating ------------------------------------------------


def test_blocked_strategy_is_not_evaluated_when_missing_kill_switch_row() -> None:
    """A strategy with no STRATEGY:{key} row at all resolves UNAVAILABLE,
    which blocks - the same as ENGAGED."""

    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(instruments=_instruments())
        strategy = RecordingStrategy(strategy_key="never-enabled", factory=factory)
        registry = StrategyRegistry()
        registry.register(strategy)

        found = await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        assert found is True
        assert strategy.calls == []

    asyncio.run(run())


def test_engaged_kill_switch_blocks_evaluation() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(),
            kill_switches=_kill_switches(
                [_strategy_switch_snapshot("fake-strategy", engaged=True)]
            ),
        )
        strategy = RecordingStrategy(strategy_key="fake-strategy", factory=factory)
        registry = StrategyRegistry()
        registry.register(strategy)

        await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        assert strategy.calls == []

    asyncio.run(run())


def test_disengaged_kill_switch_allows_evaluation() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(),
            kill_switches=_kill_switches([_disengaged("fake-strategy")]),
        )
        strategy = RecordingStrategy(strategy_key="fake-strategy", factory=factory)
        registry = StrategyRegistry()
        registry.register(strategy)

        await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        assert len(strategy.calls) == 1

    asyncio.run(run())


def test_kill_switch_read_failure_blocks_every_strategy() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(),
            kill_switches=_kill_switches([], raise_on_read=True),
        )
        a = RecordingStrategy(strategy_key="strategy-a", factory=factory)
        b = RecordingStrategy(strategy_key="strategy-b", factory=factory)
        registry = StrategyRegistry()
        registry.register(a)
        registry.register(b)

        await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        assert a.calls == []
        assert b.calls == []

    asyncio.run(run())


# --- Strategy isolation --------------------------------------------------


def test_one_strategy_failure_does_not_stop_other_strategies() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(),
            kill_switches=_kill_switches([_disengaged("failing"), _disengaged("healthy")]),
        )
        failing = RecordingStrategy(
            strategy_key="failing", raises=RuntimeError("boom"), factory=factory
        )
        healthy = RecordingStrategy(strategy_key="healthy", factory=factory)
        registry = StrategyRegistry()
        registry.register(failing)
        registry.register(healthy)

        await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        assert len(failing.calls) == 1
        assert len(healthy.calls) == 1

    asyncio.run(run())


def test_strategy_evaluate_runs_outside_a_transaction() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(), kill_switches=_kill_switches([_disengaged()])
        )
        strategy = RecordingStrategy(factory=factory)
        registry = StrategyRegistry()
        registry.register(strategy)

        await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        # The context+kill-switch transaction had already closed (0 open)
        # by the time evaluate() ran, and no proposal transaction has
        # opened yet either (this strategy returns no proposals).
        assert strategy.open_transactions_when_called == [0]

    asyncio.run(run())


def test_one_proposal_persistence_failure_does_not_prevent_later_proposals() -> None:
    """A strategy returning two proposals where the first's persistence
    raises must still see the second one attempted."""

    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(), kill_switches=_kill_switches([_disengaged("two-proposals")])
        )

        calls = {"n": 0}
        original_save = factory.trade_proposals.save

        async def flaky_save(proposal: object, *, created_by: object) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated write failure")
            await original_save(proposal, created_by=created_by)  # type: ignore[arg-type]

        factory.trade_proposals.save = flaky_save  # type: ignore[method-assign]

        strategy = RecordingStrategy(
            strategy_key="two-proposals",
            proposed_trades_by_call=[[_proposed_trade(), _proposed_trade()]],
            factory=factory,
        )
        registry = StrategyRegistry()
        registry.register(strategy)

        await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        # ordinal 0 failed, ordinal 1 succeeded.
        assert len(factory.trade_proposals.saved) == 1

    asyncio.run(run())


def test_zero_signal_evaluation_writes_nothing() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(), kill_switches=_kill_switches([_disengaged()])
        )
        strategy = RecordingStrategy(factory=factory)  # default: returns []
        registry = StrategyRegistry()
        registry.register(strategy)

        await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        assert factory.trade_proposals.saved == []
        assert factory.audit.saved == []

    asyncio.run(run())


def test_run_once_returns_false_when_no_strategy_is_registered() -> None:
    async def run() -> bool:
        factory = RecordingUnitOfWorkFactory()
        return await run_once(
            factory,
            registry=StrategyRegistry(),
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

    assert asyncio.run(run()) is False


def test_run_once_fails_closed_when_context_loading_raises() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(instruments=_instruments(raise_on_read=True))
        strategy = RecordingStrategy(factory=factory)
        registry = StrategyRegistry()
        registry.register(strategy)

        found = await run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        assert found is False
        assert strategy.calls == []

    asyncio.run(run())


def test_run_poll_cycle_delegates_to_run_once() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(), kill_switches=_kill_switches([_disengaged()])
        )
        strategy = RecordingStrategy(factory=factory)
        registry = StrategyRegistry()
        registry.register(strategy)

        found = await run_poll_cycle(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )

        assert found is True
        assert len(strategy.calls) == 1

    asyncio.run(run())


# --- run_poll_loop -------------------------------------------------------


def test_run_poll_loop_respects_max_iterations() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(), kill_switches=_kill_switches([_disengaged()])
        )
        strategy = RecordingStrategy(factory=factory)
        registry = StrategyRegistry()
        registry.register(strategy)

        await run_poll_loop(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            evaluation_interval_seconds=0.0,
            max_iterations=3,
        )

        assert len(strategy.calls) == 3

    asyncio.run(run())


def test_run_poll_loop_sleeps_between_every_iteration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike the drain-then-idle loops in atp_exec_paper/atp_worker, this
    loop always sleeps - there is no queue to drain."""
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("atp_strategy.runner.asyncio.sleep", fake_sleep)

    async def run() -> None:
        factory = RecordingUnitOfWorkFactory(
            instruments=_instruments(), kill_switches=_kill_switches([_disengaged()])
        )
        strategy = RecordingStrategy(factory=factory)
        registry = StrategyRegistry()
        registry.register(strategy)

        await run_poll_loop(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            evaluation_interval_seconds=42.0,
            max_iterations=2,
        )

    asyncio.run(run())
    assert sleep_calls == [42.0]


def test_run_poll_loop_survives_an_unexpected_run_poll_cycle_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raising_run_poll_cycle(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("atp_strategy.runner.run_poll_cycle", raising_run_poll_cycle)

    async def run() -> None:
        factory = RecordingUnitOfWorkFactory()
        await run_poll_loop(
            factory,
            registry=StrategyRegistry(),
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            evaluation_interval_seconds=0.0,
            max_iterations=1,
        )
        # No exception propagated - the loop's backstop caught it.

    asyncio.run(run())
