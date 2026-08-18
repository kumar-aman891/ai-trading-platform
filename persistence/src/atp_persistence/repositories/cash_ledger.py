"""Read/append access to `paper.cash_ledger` (docs/schemas/cash_ledger.md).

Persistence-layer only, no matching `atp_domain.ports.storage` Protocol -
mirrors `atp_persistence.repositories.kill_switches`'s precedent: a running
cash balance is an application/accounting bookkeeping concern
`atp_exec_paper` reads and appends to, not a domain type any risk rule
constructs or mutates itself (`atp_domain.risk.rule.RuleContext.available_cash`
is a plain `Money`, supplied by the caller). Append-only in practice - no
method here updates or deletes a row.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.types import Mode
from atp_persistence.models.paper import CashLedgerRow


class SqlAlchemyCashLedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_balance(self, mode: Mode) -> Decimal | None:
        """The current simulated cash balance - the `balance_after` of the
        most recently created entry for this mode, or `None` if no entry
        exists yet. `None` is treated as fail-closed/INDETERMINATE by
        callers (mirroring a missing kill-switch row), never assumed to be
        zero or unlimited - should not occur for PAPER once migration 0004
        has run."""
        result = await self._session.execute(
            select(CashLedgerRow.balance_after)
            .where(CashLedgerRow.mode == mode.value)
            .order_by(CashLedgerRow.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def append(
        self,
        *,
        entry_id: str,
        mode: Mode,
        entry_type: str,
        amount: Decimal,
        related_fill_id: str | None,
        balance_after: Decimal,
        created_at: datetime,
    ) -> None:
        self._session.add(
            CashLedgerRow(
                entry_id=entry_id,
                mode=mode.value,
                entry_type=entry_type,
                amount=amount,
                related_fill_id=related_fill_id,
                balance_after=balance_after,
                created_at=created_at,
            )
        )
        await self._session.flush()
