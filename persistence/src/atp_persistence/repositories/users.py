"""Read/write access to `core.users` (Phase 1 Step 8: authentication).

`UserRecord` is a persistence-level read projection, not a domain type -
mirrors `atp_persistence.repositories.kill_switches.KillSwitchStateSnapshot`'s
precedent (a stored row snapshot with no matching `atp_domain.ports.storage`
Protocol, documented there as "a persistence/query concern, not domain
business logic"). Authenticating a principal and mapping a role to
permissions is an application/security concern (`atp_api.security`), not a
trading-domain one, so no `atp_domain.identity` module is introduced for it.

`password_hash` never leaves this module through anything other than
`UserRecord.password_hash` itself - no method here logs it, and the field
name's "password" substring means the shared redaction pipeline
(`atp_platform.redaction`) would scrub it on the rare path where a whole
`UserRecord` were ever logged by mistake.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_persistence.models.core import UserRow


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    username: str
    password_hash: str
    role: str
    is_active: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime


def _row_to_record(row: UserRow) -> UserRecord:
    return UserRecord(
        user_id=row.user_id,
        username=row.username,
        password_hash=row.password_hash,
        role=row.role,
        is_active=row.is_active,
        must_change_password=row.must_change_password,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> UserRecord | None:
        """Case-insensitive lookup (`docs/schemas/user.md`: "unique,
        case-insensitive"), matching the `uq_users_lower_username` index."""
        result = await self._session.execute(
            select(UserRow).where(func.lower(UserRow.username) == username.lower())
        )
        row = result.scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        result = await self._session.execute(select(UserRow).where(UserRow.user_id == user_id))
        row = result.scalar_one_or_none()
        return _row_to_record(row) if row is not None else None

    async def count(self) -> int:
        """Used only by the bootstrap-admin process to enforce "no seeded
        user, ever create the first admin only once" (`docs/schemas/user.md`)."""
        result = await self._session.execute(select(func.count()).select_from(UserRow))
        return int(result.scalar_one())

    async def create(
        self,
        *,
        user_id: str,
        username: str,
        password_hash: str,
        role: str,
        must_change_password: bool,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        self._session.add(
            UserRow(
                user_id=user_id,
                username=username,
                password_hash=password_hash,
                role=role,
                is_active=True,
                must_change_password=must_change_password,
                created_at=created_at,
                updated_at=updated_at,
            )
        )

    async def update_password(
        self,
        user_id: str,
        *,
        password_hash: str,
        must_change_password: bool,
        updated_at: datetime,
    ) -> None:
        """A no-op if `user_id` no longer exists - mirrors
        `SqlAlchemySessionRepository.revoke`'s own "load, mutate if
        present" pattern rather than raising, since the row's existence
        was already established by the caller's own session-derived
        principal lookup moments earlier."""
        row = await self._session.get(UserRow, user_id)
        if row is not None:
            row.password_hash = password_hash
            row.must_change_password = must_change_password
            row.updated_at = updated_at
