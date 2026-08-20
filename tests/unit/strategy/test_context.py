"""`atp_strategy.context` - the strategy context-building layer (ADR-014
§C, Milestone 2C). No database connection is used - `build_strategy_context`
is exercised against `FakeStrategyUnitOfWork`."""

from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from decimal import Decimal

from atp_domain.clock import FrozenClock
from atp_domain.strategy import InstrumentSnapshot, StrategyContext
from atp_domain.types import InstrumentId
from atp_strategy.context import (
    SYNTHETIC_QUOTE_SOURCE,
    build_strategy_context,
    build_synthetic_quote,
)
from tests.unit.strategy.fakes import (
    FakeAuditEventWriter,
    FakeInstrumentRepository,
    FakeInstrumentRow,
    FakeKillSwitchStateRepository,
    FakeStrategyUnitOfWork,
    FakeTradeProposalRepository,
)

_AS_OF = datetime(2026, 1, 1, tzinfo=UTC)


def _fake_uow(rows: list[FakeInstrumentRow]) -> FakeStrategyUnitOfWork:
    return FakeStrategyUnitOfWork(
        instruments=FakeInstrumentRepository(rows),
        kill_switches=FakeKillSwitchStateRepository(),
        trade_proposals=FakeTradeProposalRepository(),
        audit=FakeAuditEventWriter(),
    )


def test_build_strategy_context_is_deterministic_for_the_same_inputs() -> None:
    rows = [
        FakeInstrumentRow("11111111-1111-7111-8111-111111111111", "FIXTURE", 1, Decimal("0.05"))
    ]

    async def run() -> tuple[StrategyContext, StrategyContext]:
        first = await build_strategy_context(_fake_uow(rows), as_of=_AS_OF, correlation_id="corr-1")
        second = await build_strategy_context(
            _fake_uow(rows), as_of=_AS_OF, correlation_id="corr-1"
        )
        return first, second

    first, second = asyncio.run(run())
    assert first == second


def test_build_strategy_context_uses_the_injected_as_of() -> None:
    clock = FrozenClock(_AS_OF)
    rows: list[FakeInstrumentRow] = []

    async def run() -> StrategyContext:
        return await build_strategy_context(_fake_uow(rows), as_of=clock.now(), correlation_id="c")

    context = asyncio.run(run())
    assert context.as_of == _AS_OF


def test_build_strategy_context_generates_a_correlation_id_when_absent() -> None:
    async def run() -> StrategyContext:
        return await build_strategy_context(_fake_uow([]), as_of=_AS_OF)

    context = asyncio.run(run())
    assert context.correlation_id


def test_build_strategy_context_projects_instruments_and_synthetic_quotes() -> None:
    rows = [
        FakeInstrumentRow("11111111-1111-7111-8111-111111111111", "FIXTURE", 5, Decimal("0.10"))
    ]

    async def run() -> StrategyContext:
        return await build_strategy_context(_fake_uow(rows), as_of=_AS_OF, correlation_id="c")

    context = asyncio.run(run())
    instrument_id = InstrumentId("11111111-1111-7111-8111-111111111111")
    assert context.instruments[instrument_id] == InstrumentSnapshot(
        instrument_id=instrument_id, symbol="FIXTURE", lot_size=5, tick_size=Decimal("0.10")
    )
    assert context.quotes[instrument_id].source == SYNTHETIC_QUOTE_SOURCE
    assert context.quotes[instrument_id].last_price.value > 0


def test_build_synthetic_quote_is_pure_and_deterministic() -> None:
    instrument = InstrumentSnapshot(
        instrument_id=InstrumentId("22222222-2222-7222-8222-222222222222"),
        symbol="FIXTURE",
        lot_size=1,
        tick_size=Decimal("0.05"),
    )
    first = build_synthetic_quote(instrument, as_of=_AS_OF)
    second = build_synthetic_quote(instrument, as_of=_AS_OF)
    assert first == second
    assert first.last_price.value > 0


def test_strategy_context_never_holds_a_repository_or_session_object() -> None:
    """No field on the frozen StrategyContext dataclass may carry a
    database session or repository - every field must be an
    already-materialized, plain value (ADR-014 §C)."""
    for field_name, field_type in StrategyContext.__annotations__.items():
        rendered = str(field_type)
        assert "Session" not in rendered, f"{field_name} looks session-shaped: {rendered}"
        assert "Repository" not in rendered, f"{field_name} looks repository-shaped: {rendered}"
        assert "UnitOfWork" not in rendered, f"{field_name} looks UnitOfWork-shaped: {rendered}"


def test_build_strategy_context_signature_has_no_session_parameter() -> None:
    signature = inspect.signature(build_strategy_context)
    assert "session" not in signature.parameters
