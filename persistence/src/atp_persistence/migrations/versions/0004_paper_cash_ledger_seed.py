"""Seed the opening PAPER simulated-cash DEPOSIT into `paper.cash_ledger`
(docs/schemas/cash_ledger.md: "The starting DEPOSIT is migration-seeded per
paper account, not user-configurable via any Phase 1 API route").

Without this row the PAPER cash balance is unset (not zero - genuinely
absent), and `RISK.CAPITAL.001` (`SimulatedCashSufficiencyRule`) returns
INDETERMINATE for every proposal forever, since `RuleContext.available_cash`
would have nothing to be populated from - the aggregator's reject-by-default
behavior would then reject every PAPER proposal, which is safe but useless.

docs/schemas/cash_ledger.md documents the *shape* of the opening deposit but
names no concrete amount (unlike `core.risk_config`, this table has no
existing bootstrap precedent to reuse). Mirroring migration 0001's own
`_BOOTSTRAP_MAX_ORDER_NOTIONAL` precedent (a deliberately conservative,
undocumented-elsewhere constant, not derived from any spec), this migration
picks its own round starting balance and documents it here, not silently.

`PAPER_INITIAL_CAPITAL` (below) is explicitly classified as:
- a Phase-1 deterministic paper-trading fixture, chosen only to make paper
  execution reproducible (RISK.CAPITAL.001 needs a real, non-`None` balance
  to evaluate against - see gateway.py's module docstring);
- not a real brokerage/account balance - no broker connection exists to
  have one (ADR-006, ADR-008);
- not a production risk limit - `core.risk_config.max_order_notional` is
  the risk limit; this is only the simulated cash this fixture account
  starts with;
- not an assumption about any user's actual capital - Phase 1 has exactly
  one simulated PAPER account, shared by every `paper_trader`+ user, per
  docs/schemas/cash_ledger.md's "Note" section.

Revision ID: 0004_paper_cash_ledger_seed
Revises: 0003_table_grants
Create Date: 2026-08-18
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from atp_domain.ids import UUIDv7Generator

revision: str = "0004_paper_cash_ledger_seed"
down_revision: str | None = "0003_table_grants"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None

# Ten times `_BOOTSTRAP_MAX_ORDER_NOTIONAL` (migration 0001) - enough
# simulated capital headroom for more than one proposal near the bootstrap
# per-order notional limit to be approved by RISK.CAPITAL.001 without this
# seed amount itself being the binding constraint. A deliberately
# conservative fixture value, not derived from any spec - see the module
# docstring's explicit classification of what this constant is, and is not.
PAPER_INITIAL_CAPITAL = Decimal("10000000.000000")

# Deterministic, so downgrade() can find and remove exactly this row
# without depending on the randomly generated entry_id surviving between
# separate upgrade()/downgrade() invocations (mirrors migration 0001's
# bootstrap risk_config row identification pattern).
_SEED_MARKER_ENTRY_TYPE = "DEPOSIT"


def upgrade() -> None:
    entry_id = UUIDv7Generator().new_id()
    now = datetime.now(UTC)
    # `entry_id` needs an explicit server-side cast (`CAST(:entry_id AS
    # uuid)`) for the same reason migration 0001's bootstrap risk_config
    # row does: it is bound as a plain Python `str`, SQLAlchemy infers
    # `String` for the bind parameter, and the postgresql+psycopg dialect
    # renders an explicit `::VARCHAR` cast on it - which Postgres cannot
    # implicitly coerce into the `uuid` column without help.
    op.execute(
        sa.text(
            """
            INSERT INTO paper.cash_ledger
                (entry_id, mode, entry_type, amount, related_fill_id, balance_after, created_at)
            VALUES
                (CAST(:entry_id AS uuid), 'PAPER', :entry_type, :amount, NULL, :balance_after,
                 :created_at)
            """
        ).bindparams(
            entry_id=entry_id,
            entry_type=_SEED_MARKER_ENTRY_TYPE,
            amount=PAPER_INITIAL_CAPITAL,
            balance_after=PAPER_INITIAL_CAPITAL,
            created_at=now,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM paper.cash_ledger WHERE mode = 'PAPER' AND entry_type = 'DEPOSIT' "
            "AND related_fill_id IS NULL AND amount = :amount"
        ).bindparams(amount=PAPER_INITIAL_CAPITAL)
    )
