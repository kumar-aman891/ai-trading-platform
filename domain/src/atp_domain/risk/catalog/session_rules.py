"""SESSION family - no real Phase 1 implementation (no broker session, no
market-data/calendar adapter exists yet). All four canonical IDs are
LIVE-only stubs."""

from __future__ import annotations

CANONICAL_RULES: tuple[tuple[str, str], ...] = (
    ("RISK.SESSION.001", "Broker session valid"),
    ("RISK.SESSION.002", "Exchange/segment market session open"),
    ("RISK.SESSION.003", "System clock health"),
    ("RISK.SESSION.004", "Broker connectivity health check"),
)
