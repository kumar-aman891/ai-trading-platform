"""Strategy Protocol, identity, and registry (ADR-014).

Milestone 1 of the Strategy Framework: domain-layer scaffolding only. No
runtime, no scheduler, no execution. `Strategy.evaluate()` is synchronous
and pure - all I/O (market data, instrument lookup, the clock) happens in
a future runner *before* `StrategyContext` is constructed, so a strategy
is a deterministic function of its input, matching this domain's existing
preference for pure functions (`resolve_switch_state`,
`build_kill_switch_states`).

`StrategyContext.instruments` cannot reuse
`atp_persistence.repositories.instruments.InstrumentSnapshot` (ADR-014's
own design document assumed it could) - `atp_domain` is forbidden from
importing `atp_persistence` at all (`pyproject.toml`'s "Domain kernel
stays framework-free" import-linter contract), and the persistence
layering contract places `atp_domain` innermost, below `atp_persistence`,
not the other way round. `InstrumentSnapshot` below is therefore a
domain-owned equivalent (same fields a strategy needs: identity, symbol,
lot size, tick size) that a future runner projects the persistence-layer
snapshot into, mirroring how `atp_domain.ports.marketdata.Quote` is
already a domain-owned shape independent of whatever adapter produces it.

Two identifiers per strategy, not one: `strategy_key` (a stable,
human-readable string - what a human types, what the kill switch
qualifier and logs use) and `strategy_id` (a genuine UUID, required only
because `paper.trade_proposals.strategy_id`/`audit.audit_events.strategy_id`
are real Postgres `uuid` columns, not free text). `strategy_id` is
*derived*, never stored: `uuid.uuid5(_STRATEGY_NAMESPACE, strategy_key)`
always produces the same UUID for the same key, on every process
restart, with no registry table and no migration.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from atp_domain.errors import DomainError
from atp_domain.money import Price, Quantity
from atp_domain.ports.marketdata import Quote
from atp_domain.types import InstrumentId, OrderType, Product, Side, StrategyId

#: Fixed, hardcoded namespace UUID for deriving `strategy_id` values.
#: Computed once (`uuid.uuid5(uuid.NAMESPACE_URL, "atp.strategy")`) and
#: frozen here as a literal so it can never silently change between
#: reads of this module - a different namespace would re-derive every
#: existing strategy_id.
_STRATEGY_NAMESPACE = uuid.UUID("a1c37f9a-6b93-52ee-8c9b-6e6b7e7f9b8b")


class StrategyEvaluationError(DomainError):
    """Raised by `Strategy.evaluate()` for a genuine internal failure.

    Returning an empty sequence is the normal "no signal this cycle"
    case, never an error - this exception is reserved for evaluate()
    itself being unable to complete.
    """


class DuplicateStrategyError(DomainError):
    """The same `strategy_key` was registered twice in a `StrategyRegistry`."""


def derive_strategy_id(strategy_key: str) -> StrategyId:
    """Deterministic name-based UUID (RFC 4122 §4.3): the same
    `strategy_key` always derives the same `StrategyId`, with no
    persisted mapping and no migration."""
    return StrategyId(str(uuid.uuid5(_STRATEGY_NAMESPACE, strategy_key)))


@dataclass(frozen=True, slots=True)
class InstrumentSnapshot:
    """Domain-owned instrument static data a strategy may consult.

    Deliberately independent of `atp_persistence.repositories.instruments
    .InstrumentSnapshot` (see module docstring) - a future runner projects
    the persistence-layer row into this shape before constructing a
    `StrategyContext`.
    """

    instrument_id: InstrumentId
    symbol: str
    lot_size: int
    tick_size: Decimal


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Everything `Strategy.evaluate()` may consult. All fields are
    resolved by the caller before construction - `evaluate()` itself
    performs no I/O and reads no wall clock."""

    as_of: datetime
    correlation_id: str
    instruments: Mapping[InstrumentId, InstrumentSnapshot]
    quotes: Mapping[InstrumentId, Quote]


@dataclass(frozen=True, slots=True)
class ProposedTrade:
    """What a strategy decides - deliberately not a `TradeProposal`. It
    carries no `proposal_id`/`created_at`/`created_by` (infra-assigned
    fields); a future runner converts one of these into a real
    `TradeProposal` through the existing intake path (ADR-012)."""

    instrument_id: InstrumentId
    side: Side
    quantity: Quantity
    order_type: OrderType
    limit_price: Price | None
    product: Product
    client_request_id: str
    expected_risk: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if not isinstance(self.expected_risk, MappingProxyType):
            object.__setattr__(self, "expected_risk", MappingProxyType(dict(self.expected_risk)))


@runtime_checkable
class Strategy(Protocol):
    @property
    def strategy_key(self) -> str: ...

    @property
    def strategy_version(self) -> int: ...

    def evaluate(self, context: StrategyContext) -> Sequence[ProposedTrade]: ...


class StrategyRegistry:
    """Static, code-defined registry - not a plugin/discovery mechanism.
    Built once at process start from explicit `register()` calls,
    mirroring `atp_domain.risk.registry.RuleRegistry`'s existing shape."""

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        key = strategy.strategy_key
        if key in self._strategies:
            raise DuplicateStrategyError(f"Strategy key {key!r} is already registered.")
        self._strategies[key] = strategy

    def get(self, strategy_key: str) -> Strategy | None:
        return self._strategies.get(strategy_key)

    def all(self) -> Sequence[Strategy]:
        return tuple(self._strategies.values())
