"""Read/write access to `core.sessions` (Phase 1 Step 8: authentication).

`SessionRecord` mirrors the stored row exactly - the raw session ID is
never persisted anywhere (`session_id_hash` only, per `docs/schemas/session.md`)
and this module never receives or handles a raw token itself; hashing
happens in `atp_api.security.tokens` before any call reaches here. No
domain Protocol backs this class, for the same reason given in
`atp_persistence.repositories.users`'s module docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_persistence.models.core import SessionRow


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id_hash: str
    user_id: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    ip_address: str | None


def _row_to_record(row: SessionRow) -> SessionRecord:
    return SessionRecord(
        session_id_hash=row.session_id_hash,
        user_id=row.user_id,
        csrf_token=row.csrf_token,
        created_at=row.created_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        ip_address=row.ip_address,
    )


class SqlAlchemySessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_hash(self, session_id_hash: str) -> SessionRecord | None:
        result = await self._session.execute(
            select(SessionRow).where(SessionRow.session_id_hash == session_id_hash)
        )
        row = result.scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def create(
        self,
        *,
        session_id_hash: str,
        user_id: str,
        csrf_token: str,
        created_at: datetime,
        expires_at: datetime,
        ip_address: str | None,
    ) -> None:
        self._session.add(
            SessionRow(
                session_id_hash=session_id_hash,
                user_id=user_id,
                csrf_token=csrf_token,
                created_at=created_at,
                expires_at=expires_at,
                revoked_at=None,
                ip_address=ip_address,
            )
        )

    async def extend_expiry(self, session_id_hash: str, *, new_expires_at: datetime) -> None:
        """Sliding renewal - extends `expires_at` on a still-valid session.
        Never touches an already-revoked row (callers must check
        `revoked_at`/`expires_at` themselves before calling this, since a
        blind UPDATE here would silently resurrect a revoked session)."""
        row = await self._session.get(SessionRow, session_id_hash)
        if row is not None and row.revoked_at is None:
            row.expires_at = new_expires_at

    async def revoke(self, session_id_hash: str, *, revoked_at: datetime) -> None:
        """Idempotent: revoking an already-revoked or unknown session is a
        no-op, never an error (repeated-logout must stay safe)."""
        row = await self._session.get(SessionRow, session_id_hash)
        if row is not None and row.revoked_at is None:
            row.revoked_at = revoked_at
