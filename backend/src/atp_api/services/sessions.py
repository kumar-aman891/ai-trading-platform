"""Session lifecycle: create, validate (with sliding renewal), revoke
(Phase 1 Step 8).

Pure orchestration over `atp_persistence.repositories.sessions` and
`atp_api.security.tokens`/`csrf` - no HTTP concern (`Request`/`Response`/
cookies) appears here; that translation lives in `atp_api.routers.auth`
and `atp_api.deps`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from atp_api.security.csrf import generate_csrf_token
from atp_api.security.tokens import generate_token, hash_token
from atp_domain.clock import Clock
from atp_persistence.repositories import SessionRecord, SqlAlchemySessionRepository


@dataclass(frozen=True, slots=True)
class NewSession:
    raw_token: str
    csrf_token: str
    expires_at: datetime


async def create_session(
    repository: SqlAlchemySessionRepository,
    *,
    user_id: str,
    ip_address: str | None,
    clock: Clock,
    ttl_seconds: int,
) -> NewSession:
    raw_token = generate_token()
    csrf_token = generate_csrf_token()
    now = clock.now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    await repository.create(
        session_id_hash=hash_token(raw_token),
        user_id=user_id,
        csrf_token=csrf_token,
        created_at=now,
        expires_at=expires_at,
        ip_address=ip_address,
    )
    return NewSession(raw_token=raw_token, csrf_token=csrf_token, expires_at=expires_at)


SessionValidationOutcome = Literal["OK", "MISSING", "UNKNOWN", "REVOKED", "EXPIRED"]


@dataclass(frozen=True, slots=True)
class SessionValidationResult:
    session: SessionRecord | None
    outcome: SessionValidationOutcome


async def validate_and_renew_session(
    repository: SqlAlchemySessionRepository,
    *,
    raw_token: str | None,
    clock: Clock,
    ttl_seconds: int,
) -> SessionValidationResult:
    """Malformed/absent cookie, unknown hash, revoked, and expired are all
    distinguished internally (for the caller's audit trail) but every
    non-OK outcome maps to one of exactly two generic HTTP errors
    (`SessionInvalidError`/`SessionExpiredError`, `atp_api.errors`) - none
    of this detail is echoed to the client beyond that."""
    if not raw_token:
        return SessionValidationResult(session=None, outcome="MISSING")

    session_hash = hash_token(raw_token)
    session = await repository.get_by_hash(session_hash)
    if session is None:
        return SessionValidationResult(session=None, outcome="UNKNOWN")
    if session.revoked_at is not None:
        return SessionValidationResult(session=None, outcome="REVOKED")

    now = clock.now()
    if session.expires_at <= now:
        return SessionValidationResult(session=None, outcome="EXPIRED")

    new_expires_at = now + timedelta(seconds=ttl_seconds)
    await repository.extend_expiry(session_hash, new_expires_at=new_expires_at)
    renewed = SessionRecord(
        session_id_hash=session.session_id_hash,
        user_id=session.user_id,
        csrf_token=session.csrf_token,
        created_at=session.created_at,
        expires_at=new_expires_at,
        revoked_at=None,
        ip_address=session.ip_address,
    )
    return SessionValidationResult(session=renewed, outcome="OK")


async def revoke_session_by_token(
    repository: SqlAlchemySessionRepository, *, raw_token: str, clock: Clock
) -> SessionRecord | None:
    """Returns the session that was actually revoked, or `None` if the
    token was unknown or already revoked - idempotent by construction, so
    a caller (logout) can treat every case as success while still knowing
    whether an audit event is warranted."""
    session_hash = hash_token(raw_token)
    session = await repository.get_by_hash(session_hash)
    if session is None or session.revoked_at is not None:
        return None
    await repository.revoke(session_hash, revoked_at=clock.now())
    return session
