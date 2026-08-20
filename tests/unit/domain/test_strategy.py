"""Tests for atp_domain.strategy (ADR-014 Milestone 1): the Strategy
Protocol, StrategyContext/ProposedTrade immutability, StrategyRegistry
registration/lookup/duplicate-rejection, and derive_strategy_id's
determinism. Domain-layer scaffolding only - no runtime, no I/O."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atp_domain.errors import DomainError
from atp_domain.money import Price, Quantity
from atp_domain.ports.marketdata import Quote
from atp_domain.strategy import (
    DuplicateStrategyError,
    InstrumentSnapshot,
    ProposedTrade,
    Strategy,
    StrategyContext,
    StrategyEvaluationError,
    StrategyRegistry,
    derive_strategy_id,
)
from atp_domain.types import InstrumentId, OrderType, Product, Side

_INSTRUMENT_ID = InstrumentId("11111111-1111-7111-8111-111111111111")


@dataclasses.dataclass(frozen=True, slots=True)
class _FakeStrategy:
    strategy_key: str
    strategy_version: int = 1

    def evaluate(self, context: StrategyContext) -> list[ProposedTrade]:
        return []


def _make_context() -> StrategyContext:
    snapshot = InstrumentSnapshot(
        instrument_id=_INSTRUMENT_ID, symbol="TEST", lot_size=1, tick_size=Decimal("0.05")
    )
    quote = Quote(
        instrument_id=_INSTRUMENT_ID,
        last_price=Price(Decimal("100")),
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        source="fixture",
    )
    return StrategyContext(
        as_of=datetime(2026, 1, 1, tzinfo=UTC),
        correlation_id="corr-1",
        instruments={_INSTRUMENT_ID: snapshot},
        quotes={_INSTRUMENT_ID: quote},
    )


def _make_proposed_trade(**overrides: object) -> ProposedTrade:
    defaults: dict[str, object] = {
        "instrument_id": _INSTRUMENT_ID,
        "side": Side.BUY,
        "quantity": Quantity(Decimal(10)),
        "order_type": OrderType.MARKET,
        "limit_price": None,
        "product": Product.CNC,
        "client_request_id": "strategy-req-1",
    }
    defaults.update(overrides)
    return ProposedTrade(**defaults)  # type: ignore[arg-type]


# --- StrategyEvaluationError / DuplicateStrategyError -----------------------


def test_strategy_evaluation_error_is_a_domain_error() -> None:
    assert issubclass(StrategyEvaluationError, DomainError)


def test_duplicate_strategy_error_is_a_domain_error() -> None:
    assert issubclass(DuplicateStrategyError, DomainError)


# --- derive_strategy_id -------------------------------------------------


def test_derive_strategy_id_is_deterministic_for_the_same_key() -> None:
    first = derive_strategy_id("momentum-v1")
    second = derive_strategy_id("momentum-v1")
    assert first == second


def test_derive_strategy_id_differs_across_keys() -> None:
    assert derive_strategy_id("momentum-v1") != derive_strategy_id("mean-reversion-v1")


def test_derive_strategy_id_is_a_valid_uuid_string() -> None:
    strategy_id = derive_strategy_id("momentum-v1")
    uuid.UUID(str(strategy_id))  # raises ValueError if not a valid UUID


def test_derive_strategy_id_matches_manual_uuid5_computation() -> None:
    from atp_domain.strategy import _STRATEGY_NAMESPACE

    expected = str(uuid.uuid5(_STRATEGY_NAMESPACE, "momentum-v1"))
    assert derive_strategy_id("momentum-v1") == expected


# --- StrategyContext / ProposedTrade immutability -----------------------


def test_strategy_context_is_frozen() -> None:
    context = _make_context()
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.correlation_id = "other"  # type: ignore[misc]


def test_strategy_context_constructs_from_injected_inputs() -> None:
    context = _make_context()
    assert context.instruments[_INSTRUMENT_ID].symbol == "TEST"
    assert context.quotes[_INSTRUMENT_ID].last_price == Price(Decimal("100"))


def test_proposed_trade_is_frozen() -> None:
    trade = _make_proposed_trade()
    with pytest.raises(dataclasses.FrozenInstanceError):
        trade.quantity = Quantity(Decimal(1))  # type: ignore[misc]


def test_proposed_trade_expected_risk_defaults_to_empty_mapping() -> None:
    trade = _make_proposed_trade()
    assert dict(trade.expected_risk) == {}


def test_proposed_trade_expected_risk_is_wrapped_read_only() -> None:
    trade = _make_proposed_trade(expected_risk={"note": "test"})
    with pytest.raises(TypeError):
        trade.expected_risk["note"] = "other"  # type: ignore[index]


# --- Strategy Protocol structural conformance -----------------------------


def test_fake_strategy_satisfies_the_strategy_protocol() -> None:
    strategy: Strategy = _FakeStrategy(strategy_key="momentum-v1")
    assert isinstance(strategy, Strategy)
    assert strategy.strategy_key == "momentum-v1"
    assert strategy.strategy_version == 1
    assert strategy.evaluate(_make_context()) == []


def test_an_unrelated_object_does_not_satisfy_the_strategy_protocol() -> None:
    assert not isinstance(object(), Strategy)


# --- StrategyRegistry -----------------------------------------------------


def test_registry_registers_and_retrieves_a_strategy() -> None:
    registry = StrategyRegistry()
    strategy = _FakeStrategy(strategy_key="momentum-v1")

    registry.register(strategy)

    assert registry.get("momentum-v1") is strategy


def test_registry_get_returns_none_for_an_unregistered_key() -> None:
    registry = StrategyRegistry()
    assert registry.get("nonexistent") is None


def test_registry_all_returns_every_registered_strategy() -> None:
    registry = StrategyRegistry()
    first = _FakeStrategy(strategy_key="momentum-v1")
    second = _FakeStrategy(strategy_key="mean-reversion-v1")

    registry.register(first)
    registry.register(second)

    assert set(registry.all()) == {first, second}


def test_registry_rejects_duplicate_strategy_key_registration() -> None:
    registry = StrategyRegistry()
    registry.register(_FakeStrategy(strategy_key="momentum-v1"))

    with pytest.raises(DuplicateStrategyError, match="momentum-v1"):
        registry.register(_FakeStrategy(strategy_key="momentum-v1", strategy_version=2))


def test_registry_starts_empty() -> None:
    assert StrategyRegistry().all() == ()
