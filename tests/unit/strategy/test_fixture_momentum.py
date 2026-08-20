"""`atp_strategy.strategies.fixture_momentum.FixtureMomentumStrategy` -
the Milestone 2C reference strategy. Deterministic, pure, no I/O."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from atp_domain.money import Price
from atp_domain.ports.marketdata import Quote
from atp_domain.strategy import InstrumentSnapshot, StrategyContext
from atp_domain.types import InstrumentId, OrderType, Product, Side
from atp_strategy.strategies.fixture_momentum import (
    DEFAULT_STRATEGY,
    STRATEGY_KEY,
    STRATEGY_VERSION,
    FixtureMomentumStrategy,
)

_AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
_INSTRUMENT_A = InstrumentId("11111111-1111-7111-8111-111111111111")
_INSTRUMENT_B = InstrumentId("22222222-2222-7222-8222-222222222222")


def _context(
    *, instruments: dict[InstrumentId, InstrumentSnapshot], quotes: dict[InstrumentId, Quote]
) -> StrategyContext:
    return StrategyContext(
        as_of=_AS_OF, correlation_id="corr-1", instruments=instruments, quotes=quotes
    )


def _instrument(instrument_id: InstrumentId, *, lot_size: int = 1) -> InstrumentSnapshot:
    return InstrumentSnapshot(
        instrument_id=instrument_id, symbol="FIXTURE", lot_size=lot_size, tick_size=Decimal("0.05")
    )


def _quote(instrument_id: InstrumentId, *, price: str) -> Quote:
    return Quote(
        instrument_id=instrument_id, last_price=Price(Decimal(price)), as_of=_AS_OF, source="TEST"
    )


def test_strategy_key_and_version_are_explicit_constants() -> None:
    strategy = FixtureMomentumStrategy()
    assert strategy.strategy_key == STRATEGY_KEY
    assert strategy.strategy_version == STRATEGY_VERSION
    assert isinstance(STRATEGY_KEY, str) and STRATEGY_KEY
    assert isinstance(STRATEGY_VERSION, int) and STRATEGY_VERSION > 0


def test_evaluate_proposes_for_an_instrument_with_a_quote_at_or_above_threshold() -> None:
    context = _context(
        instruments={_INSTRUMENT_A: _instrument(_INSTRUMENT_A, lot_size=3)},
        quotes={_INSTRUMENT_A: _quote(_INSTRUMENT_A, price="10")},
    )
    proposals = FixtureMomentumStrategy().evaluate(context)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.instrument_id == _INSTRUMENT_A
    assert proposal.side is Side.BUY
    assert proposal.order_type is OrderType.MARKET
    assert proposal.limit_price is None
    assert proposal.product is Product.CNC
    assert proposal.quantity.value == Decimal(3)


def test_evaluate_skips_an_instrument_with_no_quote() -> None:
    context = _context(instruments={_INSTRUMENT_A: _instrument(_INSTRUMENT_A)}, quotes={})
    proposals = FixtureMomentumStrategy().evaluate(context)
    assert proposals == []


def test_evaluate_skips_an_instrument_below_the_signal_threshold() -> None:
    context = _context(
        instruments={_INSTRUMENT_A: _instrument(_INSTRUMENT_A)},
        quotes={_INSTRUMENT_A: _quote(_INSTRUMENT_A, price="0.01")},
    )
    proposals = FixtureMomentumStrategy().evaluate(context)
    assert proposals == []


def test_evaluate_handles_multiple_instruments_deterministically_ordered() -> None:
    context = _context(
        instruments={
            _INSTRUMENT_B: _instrument(_INSTRUMENT_B),
            _INSTRUMENT_A: _instrument(_INSTRUMENT_A),
        },
        quotes={
            _INSTRUMENT_B: _quote(_INSTRUMENT_B, price="5"),
            _INSTRUMENT_A: _quote(_INSTRUMENT_A, price="5"),
        },
    )
    proposals = FixtureMomentumStrategy().evaluate(context)
    assert [p.instrument_id for p in proposals] == [_INSTRUMENT_A, _INSTRUMENT_B]


def test_evaluate_is_deterministic_for_the_same_context() -> None:
    context = _context(
        instruments={_INSTRUMENT_A: _instrument(_INSTRUMENT_A)},
        quotes={_INSTRUMENT_A: _quote(_INSTRUMENT_A, price="5")},
    )
    strategy = FixtureMomentumStrategy()
    first = strategy.evaluate(context)
    second = strategy.evaluate(context)
    assert first == second


def test_default_strategy_instance_is_a_fixture_momentum_strategy() -> None:
    assert isinstance(DEFAULT_STRATEGY, FixtureMomentumStrategy)
