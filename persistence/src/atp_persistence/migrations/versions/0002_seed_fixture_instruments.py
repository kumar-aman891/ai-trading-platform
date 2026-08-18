"""Seed ~20 FIXTURE NSE equities into core.instruments
(docs/schemas/instrument.md's Phase 1 note: "Rows are provider = 'FIXTURE',
~20 seeded NSE equities... This keeps provenance honest at the row level -
nothing claims to be live Kite data.").

IDs are minted the same way runtime code mints them
(`atp_domain.ids.UUIDv7Generator`), not a raw `gen_random_uuid()` SQL call,
so seeded rows are indistinguishable in shape from rows a real loader would
insert later.

Revision ID: 0002_seed_fixture_instruments
Revises: 0001_core_audit_paper_schema
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from atp_domain.ids import UUIDv7Generator
from atp_persistence.models.core import InstrumentRow

revision: str = "0002_seed_fixture_instruments"
down_revision: str | None = "0001_core_audit_paper_schema"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

# (symbol, name, lot_size, tick_size) - NSE EQ, CNC/MIS-eligible large caps.
# Fixture data only; no relationship to real-time or historical Kite data.
_FIXTURE_EQUITIES: tuple[tuple[str, str, int, str], ...] = (
    ("RELIANCE", "Reliance Industries Ltd", 1, "0.05"),
    ("TCS", "Tata Consultancy Services Ltd", 1, "0.05"),
    ("HDFCBANK", "HDFC Bank Ltd", 1, "0.05"),
    ("INFY", "Infosys Ltd", 1, "0.05"),
    ("ICICIBANK", "ICICI Bank Ltd", 1, "0.05"),
    ("HINDUNILVR", "Hindustan Unilever Ltd", 1, "0.05"),
    ("SBIN", "State Bank of India", 1, "0.05"),
    ("BHARTIARTL", "Bharti Airtel Ltd", 1, "0.05"),
    ("ITC", "ITC Ltd", 1, "0.05"),
    ("KOTAKBANK", "Kotak Mahindra Bank Ltd", 1, "0.05"),
    ("LT", "Larsen & Toubro Ltd", 1, "0.05"),
    ("AXISBANK", "Axis Bank Ltd", 1, "0.05"),
    ("BAJFINANCE", "Bajaj Finance Ltd", 1, "0.05"),
    ("ASIANPAINT", "Asian Paints Ltd", 1, "0.05"),
    ("MARUTI", "Maruti Suzuki India Ltd", 1, "0.05"),
    ("SUNPHARMA", "Sun Pharmaceutical Industries Ltd", 1, "0.05"),
    ("TITAN", "Titan Company Ltd", 1, "0.05"),
    ("ULTRACEMCO", "UltraTech Cement Ltd", 1, "0.05"),
    ("WIPRO", "Wipro Ltd", 1, "0.05"),
    ("NESTLEIND", "Nestle India Ltd", 1, "0.05"),
)

_TABLE: sa.Table = InstrumentRow.__table__  # type: ignore[assignment]


def upgrade() -> None:
    id_generator = UUIDv7Generator()
    now = datetime.now(UTC)
    rows = [
        {
            "instrument_id": id_generator.new_id(),
            "provider": "FIXTURE",
            "provider_instrument_token": f"FIXTURE:{symbol}",
            "exchange": "NSE",
            "segment": "EQ",
            "symbol": symbol,
            "name": name,
            "expiry": None,
            "strike": None,
            "option_type": None,
            "lot_size": lot_size,
            "tick_size": tick_size,
            "active_from": now,
            "active_to": None,
        }
        for symbol, name, lot_size, tick_size in _FIXTURE_EQUITIES
    ]
    op.bulk_insert(_TABLE, rows)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM core.instruments WHERE provider = 'FIXTURE'"))
