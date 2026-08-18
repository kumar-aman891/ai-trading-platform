"""HTTP security header baseline (docs/SECURITY.md: "secure headers").

A raw ASGI middleware, matching `atp_platform.asgi.CorrelationIdMiddleware`'s
style - no Starlette `BaseHTTPMiddleware` dependency, just the ASGI
callable protocol, so it stays trivially unit-testable with a hand-rolled
scope/receive/send.

No authentication concern lives here - this is the CORS/CSP/framing
baseline only; `backend/src/atp_api/security/` remains reserved for
Step 8 auth/session/CSRF/RBAC (see that package's own docstring).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

# A JSON API serves no HTML/scripts/styles of its own, so the strictest
# possible CSP is also the correct one - nothing here should ever need to
# load or execute anything.
_STATIC_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
)
_HSTS_HEADER = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


class SecurityHeadersMiddleware:
    """`hsts_enabled` must be derived from server-side configuration
    (`Settings.environment == "production"`, see `atp_api.app`) - HSTS on
    a plain-HTTP development server would tell browsers to refuse to
    connect over HTTP the next time, which is actively harmful in dev."""

    def __init__(self, app: ASGIApp, *, hsts_enabled: bool) -> None:
        self._app = app
        self._hsts_enabled = hsts_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        async def _send(message: Message) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.extend(_STATIC_HEADERS)
                if self._hsts_enabled:
                    headers.append(_HSTS_HEADER)
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, _send)
