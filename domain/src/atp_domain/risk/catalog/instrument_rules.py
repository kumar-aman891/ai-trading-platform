"""INSTRUMENT family - no real Phase 1 implementation (no instrument
master/permission adapter exists yet). Both canonical IDs are LIVE-only
stubs."""

from __future__ import annotations

CANONICAL_RULES: tuple[tuple[str, str], ...] = (
    ("RISK.INSTRUMENT.001", "Instrument valid and tradeable (eligibility)"),
    ("RISK.INSTRUMENT.002", "Account/segment permission check"),
)
