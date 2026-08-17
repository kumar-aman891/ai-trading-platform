"""DATA family - no real Phase 1 implementation (no market-data/news
adapter exists yet). All five canonical IDs are LIVE-only stubs."""

from __future__ import annotations

CANONICAL_RULES: tuple[tuple[str, str], ...] = (
    ("RISK.DATA.001", "Price sanity vs latest canonical quote"),
    ("RISK.DATA.002", "Data freshness threshold"),
    ("RISK.DATA.003", "Spread/liquidity threshold"),
    ("RISK.DATA.004", "Circuit/price-band sanity"),
    ("RISK.DATA.005", "News/event blackout rules"),
)
