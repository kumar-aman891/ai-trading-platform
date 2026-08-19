"""Unit tests for `atp_worker.uow` (Phase 1 Step 12 Phase B, ADR-013 §7).

Two things matter here, and neither needs a database:

1. **Which repositories the unit of work exposes.** `atp_worker`'s grants
   are the narrowest in the system - no `USAGE` on `paper`/`live` at all,
   table-level DML on `core.users`/`core.sessions` revoked. A
   `WorkerUnitOfWork` that carried `trade_proposals`/`orders`/`users`/
   `sessions` (as `atp_persistence.db.UnitOfWork` does) would hand this
   process repositories it has no privilege to use - so the attribute set
   is asserted exactly, in both directions.
2. **That `session_observations` is the narrow-projection repository**,
   never `SqlAlchemySessionRepository`. That substitution is the specific
   latent runtime failure ADR-013 and the repository's own docstring call
   out: `select(SessionRow)` requests seven columns where `atp_worker` is
   granted three.

Commit/rollback ordering is exercised against a fake session - the real
transaction semantics belong to SQLAlchemy and PostgreSQL, not to this
module.
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any

import pytest

from atp_persistence.repositories.audit_events import SqlAlchemyAuditEventRepository
from atp_persistence.repositories.audit_writer import SqlAlchemyAuditEventWriter
from atp_persistence.repositories.jobs import SqlAlchemyJobQueueRepository
from atp_persistence.repositories.session_observations import (
    SqlAlchemyWorkerSessionObservationRepository,
)
from atp_persistence.repositories.sessions import SqlAlchemySessionRepository
from atp_worker.uow import WorkerUnitOfWork, worker_unit_of_work, worker_unit_of_work_factory

_EXPECTED_REPOSITORIES = {"jobs", "audit", "audit_events", "session_observations"}

#: Repositories `atp_persistence.db.UnitOfWork` carries that `atp_worker`
#: holds no privilege to use - none may appear on WorkerUnitOfWork.
_FORBIDDEN_REPOSITORIES = {
    "trade_proposals",
    "risk_decisions",
    "order_intents",
    "orders",
    "fills",
    "positions",
    "cash_ledger",
    "users",
    "sessions",
}


class _FakeSession:
    def __init__(self) -> None:
        self.events: list[str] = []

    async def __aenter__(self) -> _FakeSession:
        self.events.append("session_open")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.events.append("session_close")
        return False

    async def commit(self) -> None:
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session
        self.calls = 0

    def __call__(self) -> _FakeSession:
        self.calls += 1
        return self._session


def _build_uow() -> WorkerUnitOfWork:
    return WorkerUnitOfWork(_FakeSession())  # type: ignore[arg-type]


# --- exposed surface ----------------------------------------------------


def test_unit_of_work_exposes_exactly_the_four_permitted_repositories() -> None:
    uow = _build_uow()

    public = {name for name in vars(uow) if not name.startswith("_")}

    assert public == _EXPECTED_REPOSITORIES


def test_unit_of_work_exposes_no_repository_the_worker_role_cannot_use() -> None:
    """The explicit reverse assertion: a future edit that pastes in
    `atp_persistence.db.UnitOfWork`'s attribute list fails here."""
    uow = _build_uow()

    for forbidden in _FORBIDDEN_REPOSITORIES:
        assert not hasattr(uow, forbidden), (
            f"WorkerUnitOfWork must not expose {forbidden!r} - atp_worker holds "
            "no privilege on it (migration 0003 / roles_and_schemas.sql.tmpl)"
        )


def test_repositories_are_the_expected_concrete_types() -> None:
    uow = _build_uow()

    assert isinstance(uow.jobs, SqlAlchemyJobQueueRepository)
    assert isinstance(uow.audit, SqlAlchemyAuditEventWriter)
    assert isinstance(uow.audit_events, SqlAlchemyAuditEventRepository)
    assert isinstance(uow.session_observations, SqlAlchemyWorkerSessionObservationRepository)


def test_session_access_is_the_narrow_projection_never_the_full_session_repository() -> None:
    """The specific substitution ADR-013 warns about: reusing
    SqlAlchemySessionRepository here would `select(SessionRow)` - all
    seven columns - and raise InsufficientPrivilege under the real
    atp_worker role."""
    uow = _build_uow()

    assert not isinstance(uow.session_observations, SqlAlchemySessionRepository)


# --- transaction lifecycle ----------------------------------------------


def test_worker_unit_of_work_commits_on_a_clean_exit() -> None:
    session = _FakeSession()
    factory = _FakeSessionFactory(session)

    async def run() -> None:
        async with worker_unit_of_work(factory) as uow:  # type: ignore[arg-type]
            assert isinstance(uow, WorkerUnitOfWork)

    asyncio.run(run())

    assert session.events == ["session_open", "commit", "session_close"]


def test_worker_unit_of_work_rolls_back_and_reraises_on_an_exception() -> None:
    session = _FakeSession()
    factory = _FakeSessionFactory(session)

    async def run() -> None:
        async with worker_unit_of_work(factory):  # type: ignore[arg-type]
            raise RuntimeError("handler blew up")

    with pytest.raises(RuntimeError, match="handler blew up"):
        asyncio.run(run())

    assert session.events == ["session_open", "rollback", "session_close"]
    assert "commit" not in session.events


def test_worker_unit_of_work_factory_binds_the_session_factory() -> None:
    """The one wiring point a process entrypoint needs: a zero-argument
    callable that opens exactly one new transaction per call."""
    session = _FakeSession()
    session_factory = _FakeSessionFactory(session)
    uow_factory: Any = worker_unit_of_work_factory(session_factory)  # type: ignore[arg-type]

    async def run() -> None:
        async with uow_factory() as uow:
            assert isinstance(uow, WorkerUnitOfWork)

    asyncio.run(run())

    assert session_factory.calls == 1
    assert session.events == ["session_open", "commit", "session_close"]
