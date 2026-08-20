"""`atp_strategy.runner`'s metric set - evaluations, kill-switch skips,
proposal writes, and cycle-load failures (Milestone 2C §6). `PLATFORM_REGISTRY`
is a genuine process-wide singleton, not reset between tests, so every
assertion here is a **delta** across one `run_once` call, never an
absolute value (mirrors `tests/unit/test_metrics.py`'s own locally-scoped-
name precedent, adapted since these metric names are fixed, not
test-local)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_domain.money import Quantity
from atp_domain.strategy import ProposedTrade, StrategyRegistry
from atp_domain.types import InstrumentId, OrderType, Product, Side
from atp_persistence.repositories import KillSwitchStateSnapshot
from atp_platform.metrics import PLATFORM_REGISTRY
from atp_strategy.runner import run_once
from tests.unit.strategy.fakes import (
    FakeInstrumentRepository,
    FakeInstrumentRow,
    FakeKillSwitchStateRepository,
    RecordingStrategy,
    RecordingUnitOfWorkFactory,
)

_AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
_INSTRUMENT_ID = InstrumentId("11111111-1111-7111-8111-111111111111")


def _sample(name: str, labels: dict[str, str]) -> float:
    return PLATFORM_REGISTRY.get_sample_value(name, labels) or 0.0


def _disengaged(strategy_key: str) -> KillSwitchStateSnapshot:
    return KillSwitchStateSnapshot(
        switch_id=f"STRATEGY:{strategy_key}",
        engaged=False,
        updated_at=_AS_OF,
        updated_by=None,
        reason=None,
    )


def _proposed_trade() -> ProposedTrade:
    return ProposedTrade(
        instrument_id=_INSTRUMENT_ID,
        side=Side.BUY,
        quantity=Quantity(Decimal(1)),
        order_type=OrderType.MARKET,
        limit_price=None,
        product=Product.CNC,
    )


def _instruments() -> FakeInstrumentRepository:
    return FakeInstrumentRepository(
        [FakeInstrumentRow(str(_INSTRUMENT_ID), "FIXTURE", 1, Decimal("0.05"))]
    )


def test_successful_evaluation_with_no_signal_increments_the_succeeded_counter() -> None:
    strategy_key = "metrics-succeeded"
    factory = RecordingUnitOfWorkFactory(
        instruments=_instruments(),
        kill_switches=FakeKillSwitchStateRepository([_disengaged(strategy_key)]),
    )
    registry = StrategyRegistry()
    registry.register(RecordingStrategy(strategy_key=strategy_key, factory=factory))

    before = _sample(
        "atp_strategy_evaluations_total", {"strategy_key": strategy_key, "outcome": "succeeded"}
    )

    asyncio.run(
        run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )
    )

    after = _sample(
        "atp_strategy_evaluations_total", {"strategy_key": strategy_key, "outcome": "succeeded"}
    )
    assert after - before == 1.0


def test_failed_evaluation_increments_the_failed_counter_not_succeeded() -> None:
    strategy_key = "metrics-failed"
    factory = RecordingUnitOfWorkFactory(
        instruments=_instruments(),
        kill_switches=FakeKillSwitchStateRepository([_disengaged(strategy_key)]),
    )
    registry = StrategyRegistry()
    registry.register(
        RecordingStrategy(strategy_key=strategy_key, raises=RuntimeError("boom"), factory=factory)
    )

    before_failed = _sample(
        "atp_strategy_evaluations_total", {"strategy_key": strategy_key, "outcome": "failed"}
    )
    before_succeeded = _sample(
        "atp_strategy_evaluations_total", {"strategy_key": strategy_key, "outcome": "succeeded"}
    )

    asyncio.run(
        run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )
    )

    after_failed = _sample(
        "atp_strategy_evaluations_total", {"strategy_key": strategy_key, "outcome": "failed"}
    )
    after_succeeded = _sample(
        "atp_strategy_evaluations_total", {"strategy_key": strategy_key, "outcome": "succeeded"}
    )
    assert after_failed - before_failed == 1.0
    assert after_succeeded - before_succeeded == 0.0


def test_kill_switch_block_increments_the_skip_counter() -> None:
    strategy_key = "metrics-blocked"
    factory = RecordingUnitOfWorkFactory(
        instruments=_instruments()
    )  # no disengage snapshot -> blocked
    registry = StrategyRegistry()
    registry.register(RecordingStrategy(strategy_key=strategy_key, factory=factory))

    before = _sample("atp_strategy_kill_switch_skips_total", {"strategy_key": strategy_key})

    asyncio.run(
        run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )
    )

    after = _sample("atp_strategy_kill_switch_skips_total", {"strategy_key": strategy_key})
    assert after - before == 1.0


def test_proposal_write_increments_the_written_counter() -> None:
    strategy_key = "metrics-write"
    factory = RecordingUnitOfWorkFactory(
        instruments=_instruments(),
        kill_switches=FakeKillSwitchStateRepository([_disengaged(strategy_key)]),
    )
    registry = StrategyRegistry()
    registry.register(
        RecordingStrategy(
            strategy_key=strategy_key,
            proposed_trades_by_call=[[_proposed_trade()]],
            factory=factory,
        )
    )

    before = _sample(
        "atp_strategy_proposal_writes_total", {"strategy_key": strategy_key, "outcome": "written"}
    )

    asyncio.run(
        run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )
    )

    after = _sample(
        "atp_strategy_proposal_writes_total", {"strategy_key": strategy_key, "outcome": "written"}
    )
    assert after - before == 1.0


def test_duplicate_proposal_replay_increments_the_replay_counter() -> None:
    strategy_key = "metrics-replay"
    factory = RecordingUnitOfWorkFactory(
        instruments=_instruments(),
        kill_switches=FakeKillSwitchStateRepository([_disengaged(strategy_key)]),
    )
    registry = StrategyRegistry()
    # Same as_of both cycles -> same cycle_epoch -> same client_request_id -> the second write is a replay.
    registry.register(
        RecordingStrategy(
            strategy_key=strategy_key,
            proposed_trades_by_call=[[_proposed_trade()], [_proposed_trade()]],
            factory=factory,
        )
    )

    before = _sample(
        "atp_strategy_proposal_writes_total", {"strategy_key": strategy_key, "outcome": "replay"}
    )

    asyncio.run(
        run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )
    )
    asyncio.run(
        run_once(
            factory,
            registry=registry,
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
        )
    )

    after = _sample(
        "atp_strategy_proposal_writes_total", {"strategy_key": strategy_key, "outcome": "replay"}
    )
    assert after - before == 1.0
