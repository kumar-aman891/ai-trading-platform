"""Market data port. No implementation in Phase 1 - no Kite/yfinance
adapter exists yet. Declared now to fix the shape ahead of the adapters
(rules/01-architecture.md's dependency ordering)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from atp_domain.money import Price
from atp_domain.types import InstrumentId


@dataclass(frozen=True, slots=True)
class Quote:
    """Shape finalized in Phase 2 alongside the canonical MarketBar
    entity (docs/schemas/README.md)."""

    instrument_id: InstrumentId
    last_price: Price
    as_of: datetime
    source: str


class MarketDataPort(Protocol):
    async def get_latest_quote(self, instrument_id: InstrumentId) -> Quote: ...
    async def get_historical_bars(
        self, instrument_id: InstrumentId, *, timeframe: str, start: datetime, end: datetime
    ) -> Sequence[object]: ...
