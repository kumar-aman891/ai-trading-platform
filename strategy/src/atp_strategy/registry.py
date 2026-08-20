"""Process-wide default strategy registry (ADR-014 §D, Milestone 2C).

A static, code-defined registry built once at import time from explicit
`register()` calls - not a plugin/discovery mechanism, not database-backed
(`atp_domain.strategy.StrategyRegistry`'s own docstring). Adding a new
strategy to this process means adding one `register()` call here, in code,
reviewed like any other change - never a runtime CRUD operation.
"""

from __future__ import annotations

from atp_domain.strategy import StrategyRegistry
from atp_strategy.strategies.fixture_momentum import DEFAULT_STRATEGY

DEFAULT_STRATEGY_REGISTRY = StrategyRegistry()
DEFAULT_STRATEGY_REGISTRY.register(DEFAULT_STRATEGY)
