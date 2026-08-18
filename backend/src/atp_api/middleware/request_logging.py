"""Structured request logging - method, path, status, duration, and
correlation ID only. Never the request body (may contain sensitive data,
docs/OBSERVABILITY.md/security/SECRET_HANDLING.md) and never headers
(Authorization/Cookie could appear there - explicitly excluded rather than
denylisted, since this middleware never reads `scope["headers"]` at all).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from atp_platform.logging import get_logger

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._logger = get_logger("atp_api.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        started_at = time.monotonic()
        status_code_holder: dict[str, int | None] = {"status_code": None}

        async def _send(message: Message) -> None:
            if message.get("type") == "http.response.start":
                status_code_holder["status_code"] = message.get("status")
            await send(message)

        await self._app(scope, receive, _send)

        self._logger.info(
            "http_request",
            method=scope.get("method"),
            path=scope.get("path"),
            status_code=status_code_holder["status_code"],
            duration_ms=round((time.monotonic() - started_at) * 1000, 2),
        )
