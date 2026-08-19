"""A minimal `AsyncSession` test double for unit-testing the two Phase 1
Step 12 Phase B repositories that need it -
`atp_persistence.repositories.jobs.SqlAlchemyJobQueueRepository` and
`atp_persistence.repositories.session_observations.
SqlAlchemyWorkerSessionObservationRepository`.

Every other `SqlAlchemy*Repository` class in this codebase has only ever
been exercised through Docker-gated integration tests
(`tests/integration/db/`) - there is no existing unit-level convention for
testing one directly, because most of them are thin enough that their only
meaningful failure modes are database-level ones (a real constraint, a
real grant) that no fake can stand in for.

These two are different: `claim_next`'s `SELECT ... FOR UPDATE SKIP
LOCKED` and `list_expired_unrevoked`'s exact three-column projection
(ADR-013) are correctness properties of the *statement this module
builds*, not of the database - a bug here (the wrong WHERE predicate, a
column added back to the session projection) is a Python-level defect,
catchable without Postgres, and worth catching before a Docker-gated run
even exists for it. `FakeAsyncSession` captures the real, unexecuted
`Select`/`Delete` construct each method builds so a test can compile and
inspect it - it never fakes locking or constraint *behavior*, which stays
Docker-only, out of scope for this pass (see the repositories' own
docstrings: "no backoff/lease-duration policy lives here").

Not a test file itself - no `test_*` function lives here, so pytest does
not collect it (mirrors `tests/unit/exec_paper/fakes.py`'s precedent).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class FakeResult:
    """Stands in for the `sqlalchemy.engine.Result` a real
    `AsyncSession.execute()` would return - only the accessor methods the
    two repositories under test actually call."""

    def __init__(
        self,
        *,
        scalar: Any = None,
        scalars_list: Sequence[Any] | None = None,
        rows: Sequence[Any] | None = None,
        rowcount: int | None = None,
    ) -> None:
        self._scalar = scalar
        self._scalars_list = list(scalars_list) if scalars_list is not None else []
        self._rows = list(rows) if rows is not None else []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> _ScalarsProxy:
        return _ScalarsProxy(self._scalars_list)

    def all(self) -> list[Any]:
        return self._rows


class _ScalarsProxy:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _FakeNestedTransaction:
    """Stands in for `AsyncSession.begin_nested()`'s async context
    manager (a `SAVEPOINT`). Never actually rolls anything back - there is
    nothing to roll back in a fake - it only lets an exception raised
    inside the `async with` block (by `FakeAsyncSession.flush()`, if
    configured to raise) propagate out exactly as a real `SAVEPOINT`
    would, which is all `enqueue_if_absent`'s `except IntegrityError`
    needs to see."""

    async def __aenter__(self) -> _FakeNestedTransaction:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class FakeAsyncSession:
    """Test-configured via `queue_result`/`seed_get`/`set_flush_raises`
    before each call into the repository under test - never inspects a
    query to decide what to return, since the two repositories under test
    never call `execute()` more than once per method, so there is nothing
    to disambiguate."""

    def __init__(self) -> None:
        self.executed_statements: list[Any] = []
        self.added: list[Any] = []
        self._next_result: FakeResult | None = None
        self._get_map: dict[Any, Any] = {}
        self._flush_raises: Exception | None = None

    def queue_result(self, result: FakeResult) -> None:
        self._next_result = result

    def seed_get(self, primary_key: Any, row: Any) -> None:
        self._get_map[primary_key] = row

    def set_flush_raises(self, exc: Exception) -> None:
        self._flush_raises = exc

    async def execute(self, statement: Any) -> FakeResult:
        self.executed_statements.append(statement)
        assert self._next_result is not None, (
            "FakeAsyncSession.execute() called without a queued result - "
            "call queue_result() first"
        )
        result, self._next_result = self._next_result, None
        return result

    async def get(self, model: Any, primary_key: Any) -> Any:
        return self._get_map.get(primary_key)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        if self._flush_raises is not None:
            exc, self._flush_raises = self._flush_raises, None
            raise exc

    def begin_nested(self) -> _FakeNestedTransaction:
        return _FakeNestedTransaction()
