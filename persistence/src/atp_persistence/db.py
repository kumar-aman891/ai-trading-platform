"""Engine/session construction and the Unit of Work boundary.

One `AsyncEngine` per service DSN (docs/schemas/README.md's per-role
connection model - `atp_api`, `atp_paper_exec`, `atp_worker` each connect
with their own least-privilege role, never `atp_owner`). Callers never see a
raw `AsyncSession` outside a `UnitOfWork` - the transaction boundary is
always explicit, matching CLAUDE.md's "keep execution idempotent" and the
plan's requirement that a failed transaction never partially persist state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atp_persistence.repositories.audit_writer import SqlAlchemyAuditEventWriter
from atp_persistence.repositories.orders import SqlAlchemyOrderRepository
from atp_persistence.repositories.risk_decisions import SqlAlchemyRiskDecisionRepository
from atp_persistence.repositories.sessions import SqlAlchemySessionRepository
from atp_persistence.repositories.trade_proposals import SqlAlchemyTradeProposalRepository
from atp_persistence.repositories.users import SqlAlchemyUserRepository


def create_engine(dsn: str, *, echo: bool = False) -> AsyncEngine:
    """Build an async engine for one service's DSN. `dsn` must use the
    `postgresql+psycopg://` driver (psycopg3 supports SQLAlchemy's asyncio
    extension natively) - the same scheme `atp_platform.config.Settings`
    already validates."""
    return create_async_engine(dsn, echo=echo, pool_pre_ping=True)


class UnitOfWork:
    """One database transaction, exposed as an async context manager.

    Repositories are attached lazily and share this unit of work's single
    `AsyncSession` - every write inside the `async with` block commits or
    rolls back together. There is no repository construction path that
    hands out a session-less repository, so a caller cannot accidentally
    mix repository calls across two different transactions.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.trade_proposals = SqlAlchemyTradeProposalRepository(session)
        self.risk_decisions = SqlAlchemyRiskDecisionRepository(session)
        self.orders = SqlAlchemyOrderRepository(session)
        self.users = SqlAlchemyUserRepository(session)
        self.sessions = SqlAlchemySessionRepository(session)
        self.audit = SqlAlchemyAuditEventWriter(session)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@asynccontextmanager
async def unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[UnitOfWork]:
    """The single entry point for obtaining a `UnitOfWork`. On a clean
    exit the transaction is committed; on any exception it is rolled back
    and the exception re-raised - a caller that forgets to call
    `uow.commit()` explicitly still gets a safe (rolled-back) default,
    never a half-applied write."""
    async with session_factory() as session:
        uow = UnitOfWork(session)
        try:
            yield uow
        except BaseException:
            await uow.rollback()
            raise
        else:
            await uow.commit()


@asynccontextmanager
async def read_only_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A session for read-only repositories (`AuditEventRepository`,
    `SqlAlchemyKillSwitchStateRepository` - Phase 1 Step 7). Always rolled
    back on exit, never committed - nothing using this session is
    permitted to write, so there is never a change to persist. Kept
    separate from `UnitOfWork`, which exists specifically for the
    write-path repositories (`trade_proposals`/`risk_decisions`/`orders`)
    and always commits on success; reusing it here would commit an
    empty/no-op transaction for every read, which is harmless but
    misleading about intent."""
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
