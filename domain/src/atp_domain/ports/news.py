"""News port. No implementation in Phase 1 - no news provider adapter
exists yet. Per rules/04-ai.md, any real implementation's output is
untrusted data and must never be treated as instructions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from atp_domain.types import InstrumentId


@dataclass(frozen=True, slots=True)
class NewsItem:
    headline: str
    publisher: str
    url: str
    published_at: datetime
    retrieved_at: datetime
    instrument_id: InstrumentId | None


class NewsPort(Protocol):
    async def get_recent(
        self, instrument_id: InstrumentId, *, since: datetime
    ) -> Sequence[NewsItem]: ...
