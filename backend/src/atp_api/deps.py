"""FastAPI dependency wiring.

No route handler ever receives a raw `AsyncSession` - `get_db_session`/
`get_unit_of_work` exist only so repository/service dependencies can build
on them; neither is ever itself used as a route parameter. This is the
enforcement point for "do not expose database sessions directly through
route handlers."

`get_db_session` (read-only, always rolled back) backs the Step 7 read
routes (`system`/`kill-switches`/`audit`). `get_unit_of_work` (Step 8,
commits on success) backs anything that mutates `core.sessions`/writes an
audit event - including, notably, session *validation* itself, since a
valid request slides the session's expiry forward (a write).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atp_api.config import ApiSettings
from atp_api.errors import (
    CsrfError,
    ForbiddenError,
    RateLimitExceededError,
    ServiceUnavailableError,
    SessionExpiredError,
    SessionInvalidError,
)
from atp_api.middleware.rate_limit import RateLimiter
from atp_api.security.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from atp_api.security.csrf import csrf_tokens_match
from atp_api.security.rbac import Permission, has_permission
from atp_api.services.auth import record_authorization_denial
from atp_api.services.sessions import validate_and_renew_session
from atp_domain.clock import Clock, UTCClock
from atp_domain.ids import IdGenerator, UUIDv7Generator
from atp_persistence.db import UnitOfWork, read_only_session, unit_of_work
from atp_persistence.repositories import (
    SqlAlchemyAuditEventRepository,
    SqlAlchemyCashLedgerRepository,
    SqlAlchemyFillRepository,
    SqlAlchemyInstrumentRepository,
    SqlAlchemyKillSwitchStateRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyPositionRepository,
    SqlAlchemyRiskDecisionRepository,
    SqlAlchemyTradeProposalRepository,
)
from atp_platform.config import Settings
from atp_platform.correlation import get_correlation_id, new_correlation_id


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_api_settings(request: Request) -> ApiSettings:
    api_settings: ApiSettings = request.app.state.api_settings
    return api_settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession] | None:
    session_factory: async_sessionmaker[AsyncSession] | None = request.app.state.session_factory
    return session_factory


def get_clock() -> Clock:
    return UTCClock()


def get_id_generator() -> IdGenerator:
    return UUIDv7Generator()


async def get_db_session(
    session_factory: Annotated[
        async_sessionmaker[AsyncSession] | None, Depends(get_session_factory)
    ],
) -> AsyncIterator[AsyncSession]:
    if session_factory is None:
        raise ServiceUnavailableError("The database is not configured for this deployment.")
    async with read_only_session(session_factory) as session:
        yield session


async def get_unit_of_work(
    session_factory: Annotated[
        async_sessionmaker[AsyncSession] | None, Depends(get_session_factory)
    ],
) -> AsyncIterator[UnitOfWork]:
    if session_factory is None:
        raise ServiceUnavailableError("The database is not configured for this deployment.")
    async with unit_of_work(session_factory) as uow:
        yield uow


async def get_audit_event_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyAuditEventRepository:
    return SqlAlchemyAuditEventRepository(session)


async def get_kill_switch_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyKillSwitchStateRepository:
    return SqlAlchemyKillSwitchStateRepository(session)


# ---------------------------------------------------------------------------
# PAPER trade-proposal intake and ledger reads (Phase 1 Step 10, ADR-012)
# ---------------------------------------------------------------------------
#
# Every dependency below is built over `get_db_session`'s read-only session
# - even `get_instrument_repository`, which `paper_proposals.submit_proposal`
# also uses for its pre-insert existence check. That check deliberately
# runs on a separate read-only session from the `UnitOfWork` the insert
# itself uses (`persistence/db.py` is unmodified by this milestone - see
# ADR-012/planning notes): a plain read has no need of a write transaction,
# and `core.instruments` has no Phase 1 delete route for the two reads to
# race against.


async def get_instrument_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyInstrumentRepository:
    return SqlAlchemyInstrumentRepository(session)


async def get_trade_proposal_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyTradeProposalRepository:
    return SqlAlchemyTradeProposalRepository(session)


async def get_risk_decision_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyRiskDecisionRepository:
    return SqlAlchemyRiskDecisionRepository(session)


async def get_order_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyOrderRepository:
    return SqlAlchemyOrderRepository(session)


async def get_fill_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyFillRepository:
    return SqlAlchemyFillRepository(session)


async def get_position_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyPositionRepository:
    return SqlAlchemyPositionRepository(session)


async def get_cash_ledger_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlAlchemyCashLedgerRepository:
    return SqlAlchemyCashLedgerRepository(session)


# ---------------------------------------------------------------------------
# Authentication / RBAC (Phase 1 Step 8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    user_id: str
    username: str
    role: str
    must_change_password: bool
    csrf_token: str


async def get_current_principal(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> AuthenticatedPrincipal:
    """Validates the session cookie (sliding-renewing it on success) and
    resolves the owning user. Every non-OK outcome maps to one of exactly
    two generic errors - `SessionInvalidError` for a missing/malformed/
    unknown/revoked cookie, `SessionExpiredError` for a recognizably
    expired one - and nothing more specific is ever returned to the
    caller (task's "safe and non-enumerating" requirement)."""
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    result = await validate_and_renew_session(
        uow.sessions,
        raw_token=raw_token,
        clock=clock,
        ttl_seconds=settings.session_ttl_seconds,
    )
    if result.outcome == "EXPIRED":
        raise SessionExpiredError()
    if result.outcome != "OK" or result.session is None:
        raise SessionInvalidError()

    user = await uow.users.get_by_id(result.session.user_id)
    if user is None or not user.is_active:
        raise SessionInvalidError()

    return AuthenticatedPrincipal(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        must_change_password=user.must_change_password,
        csrf_token=result.session.csrf_token,
    )


def get_login_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.login_rate_limiter
    return limiter


async def enforce_login_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_login_rate_limiter)],
) -> None:
    """A dedicated, stricter limiter than the general per-path one
    (`atp_api.middleware.rate_limit.RateLimitMiddleware`) - keyed by client
    IP only (no username; the body hasn't necessarily been validated yet)."""
    key = request.client.host if request.client is not None else "unknown"
    if not limiter.allow(f"login:{key}"):
        raise RateLimitExceededError()


def require_permission(
    permission: Permission,
) -> Callable[..., Awaitable[AuthenticatedPrincipal]]:
    """Every protected route declares exactly one of these - there is no
    route anywhere that checks a role string inline (`atp_api.security.rbac`)."""

    async def _dependency(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
        uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
        clock: Annotated[Clock, Depends(get_clock)],
        id_generator: Annotated[IdGenerator, Depends(get_id_generator)],
    ) -> AuthenticatedPrincipal:
        if not has_permission(principal.role, permission):
            await record_authorization_denial(
                uow,
                user_id=principal.user_id,
                correlation_id=get_correlation_id() or new_correlation_id(),
                clock=clock,
                id_generator=id_generator,
            )
            raise ForbiddenError()
        return principal

    return _dependency


async def enforce_csrf(
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> None:
    """The reusable form of the double-submit check `atp_api.services.auth
    .logout` already performs inline (`atp_api.security.csrf`'s module
    docstring: "every future authenticated state-changing route must
    follow the same rule"). Every state-changing route beyond login/logout
    runs with an authenticated session already in hand, so - like logout,
    unlike login - it is CSRF-checked unconditionally."""
    if not csrf_tokens_match(
        header_value=request.headers.get("x-csrf-token"),
        cookie_value=request.cookies.get(CSRF_COOKIE_NAME),
        expected=principal.csrf_token,
    ):
        raise CsrfError()
