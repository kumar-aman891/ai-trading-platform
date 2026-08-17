"""Clock/calendar port for trading-session awareness. No implementation in
Phase 1 - no trading-calendar source has been chosen yet (readiness report
open item). Distinct from atp_domain.clock.Clock, which is wall-clock time
only; this is exchange-session awareness."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class CalendarPort(Protocol):
    def is_market_open(self, *, exchange: str, at: datetime) -> bool: ...
    def next_session_open(self, *, exchange: str, after: datetime) -> datetime: ...
