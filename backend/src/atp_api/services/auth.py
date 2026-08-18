"""Authentication service: login, logout, current-principal resolution
(Phase 1 Step 8).

Router -> this service -> `atp_persistence.repositories.users`/`sessions`/
`audit_writer` -> `UnitOfWork`. No route handler touches a repository or an
`AsyncSession` directly (`atp_api.routers.auth`).

Every audit event written here shares the caller's `UnitOfWork` transaction
(ADR-010) - a failed login and its audit record either both persist or
neither does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atp_api.security.csrf import csrf_tokens_match
from atp_api.security.passwords import DUMMY_HASH, verify_password
from atp_api.security.tokens import hash_token
from atp_api.services.sessions import (
    NewSession,
    create_session,
    revoke_session_by_token,
)
from atp_domain.audit import (
    ACTION_AUTHORIZATION_DENIED,
    ACTION_LOGIN_FAILED,
    ACTION_LOGIN_SUCCEEDED,
    ACTION_LOGOUT,
    AuditEvent,
)
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.types import ActorType, EventId
from atp_persistence.db import UnitOfWork


def _audit_event(
    *,
    id_generator: IdGenerator,
    correlation_id: str,
    now: datetime,
    actor_type: ActorType,
    actor_id: str | None,
    action: str,
    decision: str | None,
) -> AuditEvent:
    return AuditEvent(
        event_id=EventId(id_generator.new_id()),
        correlation_id=correlation_id,
        occurred_at=now,
        recorded_at=now,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        mode=None,
        strategy_id=None,
        strategy_version=None,
        instrument_id=None,
        decision=decision,
    )


@dataclass(frozen=True, slots=True)
class LoginResult:
    ok: bool
    user_id: str | None = None
    username: str | None = None
    role: str | None = None
    must_change_password: bool = False
    session: NewSession | None = None


async def login(
    uow: UnitOfWork,
    *,
    username: str,
    password: str,
    ip_address: str | None,
    correlation_id: str,
    clock: Clock,
    id_generator: IdGenerator,
    session_ttl_seconds: int,
) -> LoginResult:
    now = clock.now()
    user = await uow.users.get_by_username(username)

    if user is None or not user.is_active:
        # Constant-time floor: verify against a fixed dummy hash so an
        # "unknown username" request costs the same wall-clock time as a
        # "known username, wrong password" one (no early return before
        # this call) - avoids a timing side channel for user enumeration.
        verify_password(DUMMY_HASH, password)
        await uow.audit.save(
            _audit_event(
                id_generator=id_generator,
                correlation_id=correlation_id,
                now=now,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                action=ACTION_LOGIN_FAILED,
                decision="REJECTED",
            )
        )
        return LoginResult(ok=False)

    if not verify_password(user.password_hash, password):
        await uow.audit.save(
            _audit_event(
                id_generator=id_generator,
                correlation_id=correlation_id,
                now=now,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                action=ACTION_LOGIN_FAILED,
                decision="REJECTED",
            )
        )
        return LoginResult(ok=False)

    new_session = await create_session(
        uow.sessions,
        user_id=user.user_id,
        ip_address=ip_address,
        clock=clock,
        ttl_seconds=session_ttl_seconds,
    )
    await uow.audit.save(
        _audit_event(
            id_generator=id_generator,
            correlation_id=correlation_id,
            now=now,
            actor_type=ActorType.USER,
            actor_id=user.user_id,
            action=ACTION_LOGIN_SUCCEEDED,
            decision="APPROVED",
        )
    )
    return LoginResult(
        ok=True,
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        must_change_password=user.must_change_password,
        session=new_session,
    )


LogoutOutcome = str  # "OK" | "CSRF_FAILED"


async def logout(
    uow: UnitOfWork,
    *,
    raw_session_token: str | None,
    csrf_header_value: str | None,
    csrf_cookie_value: str | None,
    correlation_id: str,
    clock: Clock,
    id_generator: IdGenerator,
) -> LogoutOutcome:
    """Idempotent: a missing, unknown, or already-revoked session token is
    still `"OK"` (task's "repeated/concurrent logout" safety requirement) -
    CSRF is only enforced for a *live* session, since there is nothing
    sensitive to protect once a session is already gone."""
    if not raw_session_token:
        return "OK"

    # Peek without revoking yet, so a CSRF failure never silently revokes
    # the session as a side effect of the check itself.
    session = await uow.sessions.get_by_hash(hash_token(raw_session_token))
    if session is None or session.revoked_at is not None:
        return "OK"

    if not csrf_tokens_match(
        header_value=csrf_header_value, cookie_value=csrf_cookie_value, expected=session.csrf_token
    ):
        return "CSRF_FAILED"

    revoked = await revoke_session_by_token(uow.sessions, raw_token=raw_session_token, clock=clock)
    if revoked is not None:
        now = clock.now()
        await uow.audit.save(
            _audit_event(
                id_generator=id_generator,
                correlation_id=correlation_id,
                now=now,
                actor_type=ActorType.USER,
                actor_id=revoked.user_id,
                action=ACTION_LOGOUT,
                decision="APPROVED",
            )
        )
    return "OK"


async def record_authorization_denial(
    uow: UnitOfWork,
    *,
    user_id: str,
    correlation_id: str,
    clock: Clock,
    id_generator: IdGenerator,
) -> None:
    await uow.audit.save(
        _audit_event(
            id_generator=id_generator,
            correlation_id=correlation_id,
            now=clock.now(),
            actor_type=ActorType.USER,
            actor_id=user_id,
            action=ACTION_AUTHORIZATION_DENIED,
            decision="REJECTED",
        )
    )
