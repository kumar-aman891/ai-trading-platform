"""`POST /api/v1/auth/login`, `POST /api/v1/auth/logout`,
`GET /api/v1/auth/me` (Phase 1 Step 8), `POST /api/v1/auth/password`
(Phase 1 Step 16).

No route here contains authentication business logic - each delegates to
`atp_api.services.auth`/`atp_api.services.sessions` and only handles the
HTTP-specific translation (reading/writing cookies, mapping a service
outcome to an `ApiError`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from atp_api.deps import (
    AuthenticatedPrincipal,
    enforce_csrf,
    enforce_login_rate_limit,
    get_clock,
    get_current_principal,
    get_id_generator,
    get_settings,
    get_unit_of_work,
)
from atp_api.errors import AuthenticationFailedError, CsrfError
from atp_api.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    MessageResponse,
    PasswordChangeRequest,
)
from atp_api.security.cookies import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    clear_auth_cookies,
    set_auth_cookies,
)
from atp_api.services import auth as auth_service
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_persistence.db import UnitOfWork
from atp_platform.config import Settings
from atp_platform.correlation import get_correlation_id, new_correlation_id

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


@router.post(
    "/login", response_model=LoginResponse, dependencies=[Depends(enforce_login_rate_limit)]
)
async def login(
    # No CSRF check here, deliberately: CSRF binds a token to an existing
    # session, and before login succeeds no session (and therefore no
    # `csrf_token`) exists yet for a caller to present - see
    # atp_api.security.csrf's module docstring for the full rationale and
    # what defends this route instead (SameSite=Strict + the login rate
    # limiter below).
    payload: LoginRequest,
    request: Request,
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
    id_generator: Annotated[IdGenerator, Depends(get_id_generator)],
) -> LoginResponse:
    result = await auth_service.login(
        uow,
        username=payload.username,
        password=payload.password,
        ip_address=_client_ip(request),
        correlation_id=get_correlation_id() or new_correlation_id(),
        clock=clock,
        id_generator=id_generator,
        session_ttl_seconds=settings.session_ttl_seconds,
    )
    if not result.ok or result.session is None:
        raise AuthenticationFailedError()

    set_auth_cookies(
        response,
        session_token=result.session.raw_token,
        csrf_token=result.session.csrf_token,
        max_age_seconds=settings.session_ttl_seconds,
    )
    assert (
        result.username is not None and result.role is not None
    )  # narrows for mypy; ok=True guarantees this
    return LoginResponse(
        username=result.username,
        role=result.role,  # type: ignore[arg-type]  # core.users CHECK constraint guarantees membership
        must_change_password=result.must_change_password,
        expires_at=result.session.expires_at,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    # Unlike /login, this route runs with an authenticated session already
    # in hand, so it is CSRF-protected unconditionally -
    # atp_api.services.auth.logout checks csrf_tokens_match before
    # revoking anything (atp_api.security.csrf's module docstring).
    request: Request,
    response: Response,
    uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
    clock: Annotated[Clock, Depends(get_clock)],
    id_generator: Annotated[IdGenerator, Depends(get_id_generator)],
) -> MessageResponse:
    outcome = await auth_service.logout(
        uow,
        raw_session_token=request.cookies.get(SESSION_COOKIE_NAME),
        csrf_header_value=request.headers.get("x-csrf-token"),
        csrf_cookie_value=request.cookies.get(CSRF_COOKIE_NAME),
        correlation_id=get_correlation_id() or new_correlation_id(),
        clock=clock,
        id_generator=id_generator,
    )
    if outcome == "CSRF_FAILED":
        raise CsrfError()

    clear_auth_cookies(response)
    return MessageResponse(message="logged out")


@router.get("/me", response_model=MeResponse)
async def me(
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
) -> MeResponse:
    return MeResponse(
        username=principal.username,
        role=principal.role,  # type: ignore[arg-type]  # core.users CHECK constraint guarantees membership
        must_change_password=principal.must_change_password,
    )


@router.post("/password", response_model=MessageResponse, dependencies=[Depends(enforce_csrf)])
async def change_password(
    # Runs with an authenticated session already in hand (like /logout,
    # unlike /login), so CSRF is checked unconditionally via enforce_csrf.
    # No permission is required beyond having a valid session - a user may
    # only ever change their own password.
    payload: PasswordChangeRequest,
    request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
    clock: Annotated[Clock, Depends(get_clock)],
    id_generator: Annotated[IdGenerator, Depends(get_id_generator)],
) -> MessageResponse:
    result = await auth_service.change_password(
        uow,
        user_id=principal.user_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
        current_raw_session_token=request.cookies.get(SESSION_COOKIE_NAME),
        correlation_id=get_correlation_id() or new_correlation_id(),
        clock=clock,
        id_generator=id_generator,
    )
    if not result.ok:
        raise AuthenticationFailedError()

    return MessageResponse(message="password changed")
