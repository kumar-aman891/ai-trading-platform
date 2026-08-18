"""API-level rate limiting: an abstraction (`RateLimiter`) plus a Step 7
in-memory implementation (`InMemoryRateLimiter`), and the ASGI middleware
that applies either one to every request.

The abstraction is what a future Redis-backed limiter would implement
instead - nothing in the middleware or in route/service code depends on
`InMemoryRateLimiter` specifically, only on the `RateLimiter` Protocol, so
swapping the implementation later touches this one module's construction
site (`atp_api.app.create_app`), not routes or business logic. No limiter
implementation touches domain semantics (order/proposal/risk state) - it
only ever decides whether to let an HTTP request proceed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Protocol, runtime_checkable

from atp_domain.clock import Clock, UTCClock

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

KeyFunc = Callable[[Scope], str]


@runtime_checkable
class RateLimiter(Protocol):
    def allow(self, key: str) -> bool:
        """True if a request keyed by `key` may proceed right now. Purely
        a yes/no decision - no side effect on anything outside the
        limiter's own bookkeeping."""
        ...


class InMemoryRateLimiter:
    """Fixed-window counter, per key, held in process memory. Not shared
    across worker processes - acceptable for Step 7's single-process
    foundation (a distributed limiter is explicitly deferred; see module
    docstring). Deterministic under test via an injected `Clock`
    (`atp_domain.clock.FrozenClock`), never wall-clock `time.time()`
    directly, per rules/05-testing.md's clock-injection requirement."""

    def __init__(self, *, limit: int, window_seconds: float, clock: Clock | None = None) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive.")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock or UTCClock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock.now().timestamp()
        window_start, count = self._windows.get(key, (now, 0))
        if now - window_start >= self._window_seconds:
            window_start, count = now, 0
        if count >= self._limit:
            self._windows[key] = (window_start, count)
            return False
        self._windows[key] = (window_start, count + 1)
        return True


def _default_key(scope: Scope) -> str:
    client = scope.get("client")
    host = client[0] if client else "unknown"
    return f"{host}:{scope.get('path', '')}"


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        key_func: KeyFunc = _default_key,
    ) -> None:
        self._app = app
        self._limiter = limiter
        self._key_func = key_func

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        if not self._limiter.allow(self._key_func(scope)):
            # Local import: keeps this middleware's module free of a
            # framework-level import for the one response it needs to
            # construct outside FastAPI's own exception-handling path
            # (middleware runs outside the ASGI app FastAPI wraps with its
            # exception handlers, so raising ApiError here would not be
            # caught by atp_api.errors's handlers).
            from atp_api.errors import RateLimitExceededError
            from atp_api.schemas.common import ErrorResponse
            from atp_platform.correlation import get_correlation_id

            error = RateLimitExceededError()
            body = (
                ErrorResponse(
                    code=error.code, message=error.message, correlation_id=get_correlation_id()
                )
                .model_dump_json()
                .encode("utf-8")
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": error.status_code,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self._app(scope, receive, send)
