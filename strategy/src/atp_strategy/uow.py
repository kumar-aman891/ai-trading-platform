"""Dedicated Unit of Work for `atp_strategy` (Milestone 2B, ADR-014 §A,
ADR-015).

Deliberately does *not* reuse `atp_persistence.db.UnitOfWork` - that class
also carries `users`/`sessions` repositories, privileges `atp_strategy`
never holds and never will (migration 0006 grants it exactly four
table-level privileges; `core.users`/`core.sessions` are not among them).
`StrategyUnitOfWork` exposes exactly the repositories this role's grants
permit, all sharing one `AsyncSession`/one transaction, mirroring
`atp_exec_paper.uow.PaperExecutionUnitOfWork`'s commit/rollback discipline
- duplicated here rather than generalized, so a bug in one process's
transaction wiring cannot widen the other's blast radius.

No `risk_decisions`, `order_intents`, `orders`, `fills`, `positions`,
`cash_ledger`, or `risk_config` repository is exposed - `atp_strategy`
holds no grant on any of those tables, and never will without a future
ADR reopening the boundary this one draws.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atp_persistence.repositories.audit_writer import SqlAlchemyAuditEventWriter
from atp_persistence.repositories.instruments import SqlAlchemyInstrumentRepository
from atp_persistence.repositories.kill_switches import SqlAlchemyKillSwitchStateRepository
from atp_persistence.repositories.trade_proposals import SqlAlchemyTradeProposalRepository


class StrategyUnitOfWork:
    """One database transaction, exposed as an async context manager via
    `strategy_unit_of_work` below. Never constructed with a session-less
    repository - every repository shares this instance's single
    `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.instruments = SqlAlchemyInstrumentRepository(session)
        self.kill_switches = SqlAlchemyKillSwitchStateRepository(session)
        self.trade_proposals = SqlAlchemyTradeProposalRepository(session)
        self.audit = SqlAlchemyAuditEventWriter(session)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


@asynccontextmanager
async def strategy_unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[StrategyUnitOfWork]:
    """The single entry point for obtaining a `StrategyUnitOfWork`. On a
    clean exit the transaction is committed; on any exception it is
    rolled back and the exception re-raised - mirrors
    `atp_persistence.db.unit_of_work` and `paper_execution_unit_of_work`
    exactly."""
    async with session_factory() as session:
        uow = StrategyUnitOfWork(session)
        try:
            yield uow
        except BaseException:
            await uow.rollback()
            raise
        else:
            await uow.commit()
