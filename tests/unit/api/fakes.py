"""In-memory fakes for `atp_persistence.repositories.users`/`sessions`/
`audit_writer`, duck-typed to the same method signatures as the real
SQLAlchemy-backed classes.

Used only to exercise `atp_api`'s authentication/RBAC *logic* (login,
logout, session validation, permission checks, audit-on-denial) through
real HTTP requests (`fastapi.testclient.TestClient` + FastAPI's
`dependency_overrides`) without a database - genuinely successful/expired/
revoked/CSRF-mismatched session flows are otherwise untestable without
Docker (see `tests/integration/db/`'s existing skip-gated convention,
which still owns the "does this actually round-trip through real
PostgreSQL/Alembic-migrated tables" question). Not a test file itself - no
`test_*` function lives here, so pytest does not collect it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from atp_domain.audit import AuditEvent
from atp_persistence.repositories import SessionRecord, UserRecord


class FakeUserRepository:
    def __init__(self, users: list[UserRecord] | None = None) -> None:
        self._by_id: dict[str, UserRecord] = {u.user_id: u for u in (users or [])}

    async def get_by_username(self, username: str) -> UserRecord | None:
        lowered = username.lower()
        for user in self._by_id.values():
            if user.username.lower() == lowered:
                return user
        return None

    async def get_by_id(self, user_id: str) -> UserRecord | None:
        return self._by_id.get(user_id)

    async def count(self) -> int:
        return len(self._by_id)

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
        self._by_id[user_id] = UserRecord(
            user_id=user_id,
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=True,
            must_change_password=must_change_password,
            created_at=created_at,
            updated_at=updated_at,
        )


class FakeSessionRepository:
    def __init__(self) -> None:
        self._by_hash: dict[str, SessionRecord] = {}

    async def get_by_hash(self, session_id_hash: str) -> SessionRecord | None:
        return self._by_hash.get(session_id_hash)

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
        self._by_hash[session_id_hash] = SessionRecord(
            session_id_hash=session_id_hash,
            user_id=user_id,
            csrf_token=csrf_token,
            created_at=created_at,
            expires_at=expires_at,
            revoked_at=None,
            ip_address=ip_address,
        )

    async def extend_expiry(self, session_id_hash: str, *, new_expires_at: datetime) -> None:
        existing = self._by_hash.get(session_id_hash)
        if existing is not None and existing.revoked_at is None:
            self._by_hash[session_id_hash] = SessionRecord(
                session_id_hash=existing.session_id_hash,
                user_id=existing.user_id,
                csrf_token=existing.csrf_token,
                created_at=existing.created_at,
                expires_at=new_expires_at,
                revoked_at=None,
                ip_address=existing.ip_address,
            )

    async def revoke(self, session_id_hash: str, *, revoked_at: datetime) -> None:
        existing = self._by_hash.get(session_id_hash)
        if existing is not None and existing.revoked_at is None:
            self._by_hash[session_id_hash] = SessionRecord(
                session_id_hash=existing.session_id_hash,
                user_id=existing.user_id,
                csrf_token=existing.csrf_token,
                created_at=existing.created_at,
                expires_at=existing.expires_at,
                revoked_at=revoked_at,
                ip_address=existing.ip_address,
            )


class FakeAuditEventWriter:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def save(self, event: AuditEvent) -> None:
        self.events.append(event)


@dataclass
class FakeUnitOfWork:
    users: FakeUserRepository = field(default_factory=FakeUserRepository)
    sessions: FakeSessionRepository = field(default_factory=FakeSessionRepository)
    audit: FakeAuditEventWriter = field(default_factory=FakeAuditEventWriter)
    committed: bool = False
    rolled_back: bool = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True
