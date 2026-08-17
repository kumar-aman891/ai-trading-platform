"""Fundamentals port. No implementation in Phase 1 - no fundamentals
provider adapter exists yet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from atp_domain.types import InstrumentId


@dataclass(frozen=True, slots=True)
class FundamentalRecord:
    instrument_id: InstrumentId
    fiscal_period: str
    currency: str
    metrics: Mapping[str, str]
    as_of: date
    source: str


class FundamentalsPort(Protocol):
    async def get_latest(self, instrument_id: InstrumentId) -> FundamentalRecord | None: ...
