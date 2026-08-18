"""API error model and exception -> HTTP response mapping.

Every error response has the same shape (`schemas.common.ErrorResponse`):
a stable machine-readable `code`, a human-readable `message` that never
contains a traceback, SQL text, a DSN, or a credential, and the request's
`correlation_id`. Unexpected exceptions are logged in full (through the
redacted structlog pipeline - atp_platform.logging/redaction) before the
sanitized response is returned.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from atp_domain.errors import DomainError
from atp_platform.correlation import get_correlation_id
from atp_platform.logging import get_logger

_logger = get_logger("atp_api.errors")


class ApiError(Exception):
    """Base class for every error this API raises deliberately. Subclasses
    set `code`/`status_code`/`message` as class attributes; an instance
    may override `message` via its constructor argument."""

    code: str = "API_ERROR"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class NotFoundError(ApiError):
    code = "NOT_FOUND"
    status_code = status.HTTP_404_NOT_FOUND
    message = "The requested resource was not found."


class ServiceUnavailableError(ApiError):
    code = "SERVICE_UNAVAILABLE"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "A required dependency is currently unavailable."


class RateLimitExceededError(ApiError):
    code = "RATE_LIMIT_EXCEEDED"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Too many requests."


class AuthenticationFailedError(ApiError):
    """Deliberately identical for "unknown username" and "wrong password"
    (Phase 1 Step 8: docs/SECURITY.md "avoid user-enumeration
    differences")."""

    code = "AUTHENTICATION_FAILED"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "Authentication failed."


class SessionInvalidError(ApiError):
    """Covers a missing, malformed, unknown, or revoked session cookie -
    none of those cases are distinguished in the response."""

    code = "SESSION_INVALID"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "The session is invalid."


class SessionExpiredError(ApiError):
    code = "SESSION_EXPIRED"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "The session has expired."


class CsrfError(ApiError):
    code = "CSRF_FAILED"
    status_code = status.HTTP_403_FORBIDDEN
    message = "CSRF validation failed."


class ForbiddenError(ApiError):
    code = "FORBIDDEN"
    status_code = status.HTTP_403_FORBIDDEN
    message = "You do not have permission to perform this action."


class ConflictError(ApiError):
    """Distinct from the generic `IntegrityError` handler's 409 below - a
    proposal re-submitted with the same `client_request_id` but different
    fields (Phase 1 Step 10's idempotency rule, `docs/schemas/order.md`) is
    an application-level conflict the service detects and raises
    deliberately, not a raw database constraint violation."""

    code = "PROPOSAL_CONFLICT"
    status_code = status.HTTP_409_CONFLICT
    message = "A proposal with this client_request_id already exists with different parameters."


class UnknownInstrumentError(ApiError):
    code = "UNKNOWN_INSTRUMENT"
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    message = "instrument_id does not refer to a known instrument."


def _error_body(*, code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "correlation_id": get_correlation_id()}


async def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # narrows for mypy; registered only for ApiError
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code=exc.code, message=exc.message),
    )


async def _handle_request_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Pydantic v2's `exc.errors()` includes an "input" key per error - the
    # raw submitted value. For most fields that's harmless, but a body like
    # `atp_api.schemas.auth.LoginRequest` has a `password` field, and a
    # validation failure on it (too short/too long) would otherwise echo
    # the submitted password straight back in this response
    # (security/SECRET_HANDLING.md: "never ... returned by any API
    # response"). Stripped unconditionally, for every field, rather than
    # only for a denylist of field names - the same "don't rely on
    # enumerating every sensitive name" reasoning as
    # atp_platform.redaction's key-denylist-plus-pattern-match design.
    sanitized_errors = [
        {key: value for key, value in error.items() if key != "input"} for error in exc.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(
            code="REQUEST_VALIDATION_ERROR",
            message="The request did not match the expected shape.",
        )
        | {"errors": sanitized_errors},
    )


async def _handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    # Domain error messages are static, developer-authored strings with no
    # user input or secrets interpolated into them (see atp_domain.errors
    # and every __post_init__ that raises one) - safe to return verbatim.
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(code="DOMAIN_VALIDATION_ERROR", message=str(exc)),
    )


async def _handle_integrity_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IntegrityError)
    # Never include str(exc) - it contains the failing SQL statement and
    # bound parameter values.
    _logger.warning(
        "integrity_error", correlation_id=get_correlation_id(), exc_class=exc.__class__.__name__
    )
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=_error_body(
            code="INTEGRITY_ERROR", message="The request conflicts with existing data."
        ),
    )


async def _handle_sqlalchemy_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SQLAlchemyError)
    # Covers connection failures, timeouts, etc. str(exc) can contain the
    # DSN/hostname (security/SECRET_HANDLING.md) - logged separately at
    # DEBUG-equivalent detail server-side only, never in the response.
    _logger.error(
        "database_error", correlation_id=get_correlation_id(), exc_class=exc.__class__.__name__
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_error_body(
            code="SERVICE_UNAVAILABLE", message="A required dependency is currently unavailable."
        ),
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    _logger.error(
        "unhandled_exception",
        correlation_id=get_correlation_id(),
        exc_class=exc.__class__.__name__,
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(code="INTERNAL_ERROR", message="An unexpected error occurred."),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Order matters only in that FastAPI dispatches by the most specific
    matching exception class first, then falls back to `Exception` - so
    the catch-all handler below is safe as a final backstop."""
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(IntegrityError, _handle_integrity_error)
    app.add_exception_handler(SQLAlchemyError, _handle_sqlalchemy_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
