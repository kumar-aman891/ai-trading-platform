"""Builds a `StrategyContext` for one evaluation cycle (ADR-014 §C,
Milestone 2C).

All I/O happens here, before `Strategy.evaluate()` is ever called - the
strategy itself receives an already-resolved, frozen `StrategyContext`
and performs no I/O of its own (`atp_domain.strategy`'s own invariant).
Nothing in the returned context holds a database session or repository -
every field is a plain, already-materialized value.

No real market-data adapter exists yet (Phase 2 concern per
`atp_domain.ports.marketdata.MarketDataPort`'s own docstring; also
CLAUDE.md rule #8 - never silently substitute a data source for a failed
canonical one). Quotes here are therefore explicitly synthetic: a pure,
deterministic function of each instrument's own `tick_size`, sourced as
`"FIXTURE_SYNTHETIC"` so nothing downstream can ever mistake this for a
real feed - this milestone's registered strategy exists to prove the
framework, not to trade on real prices.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from atp_domain.money import Price
from atp_domain.ports.marketdata import Quote
from atp_domain.strategy import InstrumentSnapshot, StrategyContext
from atp_domain.types import InstrumentId
from atp_persistence.models.core import InstrumentRow
from atp_platform.correlation import new_correlation_id
from atp_strategy.uow import StrategyUnitOfWork

#: Provenance tag for the synthetic quotes this module builds - never used
#: by any real adapter, so its presence in a `Quote.source` field is
#: itself proof no canonical data source was silently substituted.
SYNTHETIC_QUOTE_SOURCE = "FIXTURE_SYNTHETIC"

#: The synthetic last_price is this fixed multiple of the instrument's own
#: tick_size - deterministic, always strictly positive (Price's own
#: invariant), and stable across every call for the same instrument.
_SYNTHETIC_PRICE_TICK_MULTIPLE = Decimal(1000)


def _project_instrument(row: InstrumentRow) -> InstrumentSnapshot:
    """Projects a persistence-layer `core.instruments` row into the
    domain-owned `InstrumentSnapshot` (ADR-014 §C) - `atp_domain` may not
    import `atp_persistence` at all, so this projection lives here, not in
    the domain module."""
    return InstrumentSnapshot(
        instrument_id=InstrumentId(row.instrument_id),
        symbol=row.symbol,
        lot_size=row.lot_size,
        tick_size=row.tick_size,
    )


def build_synthetic_quote(instrument_snapshot: InstrumentSnapshot, *, as_of: datetime) -> Quote:
    """Pure and deterministic: no randomness, no wall-clock read (`as_of`
    is injected by the caller, never read here). Takes the already-typed
    `InstrumentSnapshot`, never a bare identifier - safety invariant #19's
    parameter-name scan treats `instrument`/`instrument_id` as forbidden
    on any public function precisely to keep every such signature this
    explicit."""
    return Quote(
        instrument_id=instrument_snapshot.instrument_id,
        last_price=Price(instrument_snapshot.tick_size * _SYNTHETIC_PRICE_TICK_MULTIPLE),
        as_of=as_of,
        source=SYNTHETIC_QUOTE_SOURCE,
    )


async def build_strategy_context(
    uow: StrategyUnitOfWork, *, as_of: datetime, correlation_id: str | None = None
) -> StrategyContext:
    """The runner's sole read boundary for one evaluation cycle - every
    active instrument, projected into the domain shape, paired with a
    synthetic quote. Called once per cycle, outside any strategy's
    `evaluate()` call (ADR-014 §C)."""
    rows = await uow.instruments.list_active()

    instruments: dict[InstrumentId, InstrumentSnapshot] = {}
    quotes: dict[InstrumentId, Quote] = {}
    for row in rows:
        snapshot = _project_instrument(row)
        instruments[snapshot.instrument_id] = snapshot
        quotes[snapshot.instrument_id] = build_synthetic_quote(snapshot, as_of=as_of)

    return StrategyContext(
        as_of=as_of,
        correlation_id=correlation_id or new_correlation_id(),
        instruments=instruments,
        quotes=quotes,
    )
