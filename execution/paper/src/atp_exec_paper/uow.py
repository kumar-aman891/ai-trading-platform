"""Dedicated Unit of Work for `atp_exec_paper` (Phase 1 Step 9).

Deliberately does *not* reuse `atp_persistence.db.UnitOfWork` - that class
also carries `users`/`sessions` repositories, privileges `atp_paper_exec`
never holds (migration 0003 revokes them entirely) and this process has no
legitimate use for. `PaperExecutionUnitOfWork` exposes exactly the
repositories this milestone's gateway needs, all sharing one
`AsyncSession`/one transaction (ADR-010) - the same commit/rollback
discipline as `atp_persistence.db.unit_of_work`, duplicated here rather than
generalized, so a bug in one process's transaction wiring cannot widen the
other's blast radius.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atp_persistence.repositories.audit_writer import SqlAlchemyAuditEventWriter
from atp_persistence.repositories.cash_ledger import SqlAlchemyCashLedgerRepository
from atp_persistence.repositories.fills import SqlAlchemyFillRepository
from atp_persistence.repositories.instruments import SqlAlchemyInstrumentRepository
from atp_persistence.repositories.kill_switches import SqlAlchemyKillSwitchStateRepository
from atp_persistence.repositories.order_intents import SqlAlchemyOrderIntentRepository
from atp_persistence.repositories.orders import SqlAlchemyOrderRepository
from atp_persistence.repositories.positions import SqlAlchemyPositionRepository
from atp_persistence.repositories.risk_config import SqlAlchemyRiskConfigRepository
from atp_persistence.repositories.risk_decisions import SqlAlchemyRiskDecisionRepository
from atp_persistence.repositories.trade_proposals import SqlAlchemyTradeProposalRepository


class PaperExecutionUnitOfWork:
    """One database transaction, exposed as an async context manager via
    `paper_execution_unit_of_work` below. Never constructed with a
    session-less repository - every repository shares this instance's
    single `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.trade_proposals = SqlAlchemyTradeProposalRepository(session)
        self.risk_decisions = SqlAlchemyRiskDecisionRepository(session)
        self.order_intents = SqlAlchemyOrderIntentRepository(session)
        self.orders = SqlAlchemyOrderRepository(session)
        self.fills = SqlAlchemyFillRepository(session)
        self.positions = SqlAlchemyPositionRepository(session)
        self.cash_ledger = SqlAlchemyCashLedgerRepository(session)
        self.instruments = SqlAlchemyInstrumentRepository(session)
        self.risk_config = SqlAlchemyRiskConfigRepository(session)
        self.kill_switches = SqlAlchemyKillSwitchStateRepository(session)
        self.audit = SqlAlchemyAuditEventWriter(session)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


@asynccontextmanager
async def paper_execution_unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[PaperExecutionUnitOfWork]:
    """The single entry point for obtaining a `PaperExecutionUnitOfWork`.
    On a clean exit the transaction is committed; on any exception it is
    rolled back and the exception re-raised - mirrors
    `atp_persistence.db.unit_of_work` exactly."""
    async with session_factory() as session:
        uow = PaperExecutionUnitOfWork(session)
        try:
            yield uow
        except BaseException:
            await uow.rollback()
            raise
        else:
            await uow.commit()
