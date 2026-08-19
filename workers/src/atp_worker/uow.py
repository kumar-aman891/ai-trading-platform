"""Dedicated Unit of Work for `atp_worker` (Phase 1 Step 12 Phase B).

Deliberately does *not* reuse `atp_persistence.db.UnitOfWork` - that class
carries `trade_proposals`/`orders`/`users`/`sessions` repositories,
privileges `atp_worker` either has revoked entirely or lacks schema
`USAGE` for, and this process has no legitimate use for. The same
reasoning `atp_exec_paper.uow.PaperExecutionUnitOfWork` records for its
own process applies here, and more sharply, because `atp_worker`'s grants
are the narrowest of any role in the system:

- no `USAGE` on `paper` or `live` at all
  (`ops/sql/roles_and_schemas.sql.tmpl`), so every `paper.*` repository is
  unreachable, not merely unused;
- table-level DML on `core.users`/`core.sessions` revoked outright
  (`0003_table_grants.py`), replaced for `core.sessions` by a
  *column-scoped* `SELECT` on three columns - which is why
  `session_observations` below is
  `SqlAlchemyWorkerSessionObservationRepository` and never
  `SqlAlchemySessionRepository`, whose `select(SessionRow)` requests all
  seven columns and raises `InsufficientPrivilege` under this role.

`WorkerUnitOfWork` therefore exposes exactly four repositories - the
complete set `atp_worker` holds grants to use - all sharing one
`AsyncSession`/one transaction (ADR-010). The commit/rollback discipline
is duplicated from `atp_persistence.db.unit_of_work` rather than
generalized, for the same reason `atp_exec_paper` duplicates it: a bug in
one process's transaction wiring must not widen another's blast radius.

ADR-013 §3 requires three *separate* transactions per job (claim, handler,
failure bookkeeping). This class is one transaction; `atp_worker.runner`
opens it three times rather than holding one open across all three phases.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from functools import partial

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atp_persistence.repositories.audit_events import SqlAlchemyAuditEventRepository
from atp_persistence.repositories.audit_writer import SqlAlchemyAuditEventWriter
from atp_persistence.repositories.jobs import SqlAlchemyJobQueueRepository
from atp_persistence.repositories.session_observations import (
    SqlAlchemyWorkerSessionObservationRepository,
)


class WorkerUnitOfWork:
    """One database transaction, exposed as an async context manager via
    `worker_unit_of_work` below. Never constructed with a session-less
    repository - every repository shares this instance's single
    `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.jobs = SqlAlchemyJobQueueRepository(session)
        self.audit = SqlAlchemyAuditEventWriter(session)
        self.audit_events = SqlAlchemyAuditEventRepository(session)
        self.session_observations = SqlAlchemyWorkerSessionObservationRepository(session)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


@asynccontextmanager
async def worker_unit_of_work(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[WorkerUnitOfWork]:
    """The single entry point for obtaining a `WorkerUnitOfWork`. On a
    clean exit the transaction is committed; on any exception it is rolled
    back and the exception re-raised - mirrors
    `atp_exec_paper.uow.paper_execution_unit_of_work` and
    `atp_persistence.db.unit_of_work` exactly."""
    async with session_factory() as session:
        uow = WorkerUnitOfWork(session)
        try:
            yield uow
        except BaseException:
            await uow.rollback()
            raise
        else:
            await uow.commit()


#: What `atp_worker.runner` actually depends on: a zero-argument callable
#: that opens one new transaction. Naming the seam explicitly (rather than
#: passing a `session_factory` down and letting each function reach for
#: `worker_unit_of_work` itself) is what makes ADR-013 §3's "three
#: transactions, never one" visible in `runner`'s own signatures - each
#: function that opens a transaction says so by taking this parameter.
UnitOfWorkFactory = Callable[[], AbstractAsyncContextManager[WorkerUnitOfWork]]


def worker_unit_of_work_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> UnitOfWorkFactory:
    """Binds a `session_factory` into the zero-argument
    `UnitOfWorkFactory` `runner` expects. The one wiring point a process
    entrypoint needs."""
    return partial(worker_unit_of_work, session_factory)
