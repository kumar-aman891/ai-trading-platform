"""ASGI correlation middleware.

A raw ASGI3 middleware, deliberately dependency-free - it implements the
ASGI callable protocol directly rather than depending on FastAPI/Starlette,
so atp_platform stays usable by any ASGI app (or none yet, since
atp_api is still an empty stub - this is infrastructure ready to be wired
in at Phase 1 Step 13).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from atp_platform.correlation import new_correlation_id, reset_correlation_id, set_correlation_id

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

DEFAULT_HEADER_NAME = b"x-correlation-id"


class CorrelationIdMiddleware:
    """Reads an inbound `X-Correlation-ID` header if present, otherwise
    generates one. Binds it to the current context for the lifetime of the
    request (so every log record emitted while handling it carries the same
    ID) and echoes it back in the response headers."""

    def __init__(self, app: ASGIApp, header_name: bytes = DEFAULT_HEADER_NAME) -> None:
        self._app = app
        self._header_name = header_name.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        correlation_id = self._extract(scope) or new_correlation_id()
        token = set_correlation_id(correlation_id)
        try:
            await self._app(scope, receive, self._wrap_send(send, correlation_id))
        finally:
            reset_correlation_id(token)

    def _extract(self, scope: Scope) -> str | None:
        for name, value in scope.get("headers") or ():
            if (
                isinstance(name, bytes)
                and isinstance(value, bytes)
                and name.lower() == self._header_name
            ):
                return value.decode("latin-1")
        return None

    def _wrap_send(self, send: Send, correlation_id: str) -> Send:
        async def _send(message: Message) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((self._header_name, correlation_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        return _send
