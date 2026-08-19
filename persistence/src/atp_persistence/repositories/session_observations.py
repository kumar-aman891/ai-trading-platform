"""Read-only projection of `core.sessions` for `atp_worker`'s
`SESSION_REAP` job type (ADR-013 §2).

Load-bearing, not cosmetic: `atp_worker` holds column-scoped `SELECT` on
exactly `(session_id_hash, expires_at, revoked_at)` of `core.sessions`
(migration `0003_table_grants.py` GRANTs those three columns only, and
REVOKEs table-level DML entirely). `atp_persistence.repositories.sessions.
SqlAlchemySessionRepository.get_by_hash` does `select(SessionRow)`, which
requests all seven columns - that raises `psycopg.errors.
InsufficientPrivilege` under the real `atp_worker` role, invisibly to any
unit test with a fake, and is exactly the trap this module exists to
avoid. Reusing `SqlAlchemySessionRepository` from `atp_worker` is a latent
runtime failure, not a style choice to avoid.

`SESSION_REAP` observes; it never mutates a session (ADR-013 §2 corrects
`docs/schemas/session.md`'s earlier "reaper job" language - the column
grant was always this narrow, only the wording was wrong). No `save`/
`revoke`/`delete` method exists here, and none should: `atp_worker` holds
no INSERT/UPDATE/DELETE grant on `core.sessions` to back one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_persistence.models.core import SessionRow


@dataclass(frozen=True, slots=True)
class SessionExpiryObservation:
    """Exactly the three columns `atp_worker` is granted - not a subset
    chosen for convenience, the complete set it is possible to expose
    without widening the grant."""

    session_id_hash: str
    expires_at: datetime
    revoked_at: datetime | None


class SqlAlchemyWorkerSessionObservationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_expired_unrevoked(self, *, now: datetime) -> Sequence[SessionExpiryObservation]:
        """The one query `SESSION_REAP` needs (ADR-013 §2): sessions past
        `expires_at` that were never explicitly revoked. Selects the
        three granted columns individually
        (`select(SessionRow.session_id_hash, SessionRow.expires_at,
        SessionRow.revoked_at)`) rather than the mapped `SessionRow`
        entity - the difference is exactly what stands between this
        query succeeding and `InsufficientPrivilege` under the real
        `atp_worker` role. The caller (a future `SESSION_REAP` handler)
        is expected to take `len(...)` of the result for its count
        metric and log line - this method returns rows, not a count,
        because a scalar `count(*)` result cannot be unit-tested against
        "selects only these three columns" the way an explicit column
        list can."""
        result = await self._session.execute(
            select(SessionRow.session_id_hash, SessionRow.expires_at, SessionRow.revoked_at).where(
                SessionRow.expires_at < now, SessionRow.revoked_at.is_(None)
            )
        )
        return [
            SessionExpiryObservation(
                session_id_hash=row.session_id_hash,
                expires_at=row.expires_at,
                revoked_at=row.revoked_at,
            )
            for row in result.all()
        ]
