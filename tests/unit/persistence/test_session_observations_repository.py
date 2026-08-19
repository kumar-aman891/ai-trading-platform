"""Unit tests for `atp_persistence.repositories.session_observations.
SqlAlchemyWorkerSessionObservationRepository` (Phase 1 Step 12 Phase B,
ADR-013 §2).

The one property that actually matters here - and the one no test with a
mocked-out `select(SessionRow)` could ever catch - is which *columns* the
generated SQL asks for. `atp_worker` holds column-scoped `SELECT` on
exactly `(session_id_hash, expires_at, revoked_at)` of `core.sessions`
(migration `0003_table_grants.py`); a query that mentions any other column
of that table raises `psycopg.errors.InsufficientPrivilege` under the real
role, invisibly to a test that only checks Python-level return values. So
these tests compile the actual statement this module builds and assert on
its column list directly, not just on what comes back.

No Docker needed. Follows `tests/unit/exec_paper/`'s convention: no
pytest-asyncio, `asyncio.run()` inside plain sync test functions.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from atp_persistence.repositories.session_observations import (
    SessionExpiryObservation,
    SqlAlchemyWorkerSessionObservationRepository,
)
from tests.unit.persistence.fakes import FakeAsyncSession, FakeResult

_NOW = datetime(2026, 1, 1, tzinfo=UTC)

# Every column core.sessions has *other* than the three atp_worker is
# granted (migration 0003_table_grants.py / SessionRow, Phase 1 Step 8) -
# none of these may ever appear in the compiled statement.
_FORBIDDEN_COLUMNS = ("user_id", "csrf_token", "created_at", "ip_address")


def _compiled(statement: object) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]


def test_list_expired_unrevoked_selects_only_the_three_granted_columns() -> None:
    session = FakeAsyncSession()
    session.queue_result(FakeResult(rows=[]))
    repo = SqlAlchemyWorkerSessionObservationRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.list_expired_unrevoked(now=_NOW))

    compiled = _compiled(session.executed_statements[0])
    assert "sessions.session_id_hash" in compiled
    assert "sessions.expires_at" in compiled
    assert "sessions.revoked_at" in compiled
    for forbidden in _FORBIDDEN_COLUMNS:
        assert f"sessions.{forbidden}" not in compiled, (
            f"query must never select core.sessions.{forbidden} - "
            "atp_worker has no column-level grant on it"
        )


def test_list_expired_unrevoked_never_selects_the_full_session_row() -> None:
    """Guards specifically against the regression this module exists to
    avoid: reusing `select(SessionRow)` (the full seven-column entity)
    instead of the three-column projection."""
    session = FakeAsyncSession()
    session.queue_result(FakeResult(rows=[]))
    repo = SqlAlchemyWorkerSessionObservationRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.list_expired_unrevoked(now=_NOW))

    compiled = _compiled(session.executed_statements[0])
    assert (
        "SELECT core.sessions.session_id_hash, core.sessions.expires_at, "
        "core.sessions.revoked_at" in compiled
    )


def test_list_expired_unrevoked_filters_on_expiry_and_never_revoked() -> None:
    session = FakeAsyncSession()
    session.queue_result(FakeResult(rows=[]))
    repo = SqlAlchemyWorkerSessionObservationRepository(session)  # type: ignore[arg-type]

    asyncio.run(repo.list_expired_unrevoked(now=_NOW))

    compiled = _compiled(session.executed_statements[0])
    assert "sessions.expires_at < " in compiled
    assert "sessions.revoked_at IS NULL" in compiled


def test_list_expired_unrevoked_maps_rows_to_observations() -> None:
    class _Row:
        def __init__(self, session_id_hash: str, expires_at: datetime, revoked_at: datetime | None):
            self.session_id_hash = session_id_hash
            self.expires_at = expires_at
            self.revoked_at = revoked_at

    rows = [_Row("hash-a", _NOW, None), _Row("hash-b", _NOW, None)]
    session = FakeAsyncSession()
    session.queue_result(FakeResult(rows=rows))
    repo = SqlAlchemyWorkerSessionObservationRepository(session)  # type: ignore[arg-type]

    observations = asyncio.run(repo.list_expired_unrevoked(now=_NOW))

    assert list(observations) == [
        SessionExpiryObservation(session_id_hash="hash-a", expires_at=_NOW, revoked_at=None),
        SessionExpiryObservation(session_id_hash="hash-b", expires_at=_NOW, revoked_at=None),
    ]


def test_list_expired_unrevoked_returns_empty_sequence_when_none_match() -> None:
    session = FakeAsyncSession()
    session.queue_result(FakeResult(rows=[]))
    repo = SqlAlchemyWorkerSessionObservationRepository(session)  # type: ignore[arg-type]

    observations = asyncio.run(repo.list_expired_unrevoked(now=_NOW))

    assert list(observations) == []
