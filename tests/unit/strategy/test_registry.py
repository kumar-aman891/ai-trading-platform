"""`atp_strategy.registry.DEFAULT_STRATEGY_REGISTRY` - the process-wide
default registry (ADR-014 §D, Milestone 2C). Static and code-defined, not
a plugin/discovery mechanism."""

from __future__ import annotations

from atp_strategy.registry import DEFAULT_STRATEGY_REGISTRY
from atp_strategy.strategies.fixture_momentum import DEFAULT_STRATEGY, STRATEGY_KEY


def test_default_registry_contains_the_reference_strategy() -> None:
    assert DEFAULT_STRATEGY_REGISTRY.get(STRATEGY_KEY) is DEFAULT_STRATEGY


def test_default_registry_all_returns_exactly_one_strategy() -> None:
    assert len(DEFAULT_STRATEGY_REGISTRY.all()) == 1
