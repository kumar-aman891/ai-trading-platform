"""Structured request logging - method, path, status, duration, and
correlation ID only. Never the request body (may contain sensitive data,
docs/OBSERVABILITY.md/security/SECRET_HANDLING.md) and never headers
(Authorization/Cookie could appear there - explicitly excluded rather than
denylisted, since this middleware never reads `scope["headers"]` at all).

Also the one call site for `atp_api`'s HTTP request metrics (Phase 1
Step 13, observability foundation) - the same values this middleware was
already computing for the log line, moved into a counter/histogram too,
not a second measurement. Labeled by `method` and `status_code` only,
deliberately never `path`: `scope["path"]` is the resolved request path
with any real ID substituted (e.g. a live `proposal_id`), not a route
template, and a Prometheus label taking one value per distinct ID is
exactly the unbounded-cardinality mistake metric labels exist to avoid.
The log line already carries the literal `path` for that reason - grep
logs for per-route detail; the metric answers "how much traffic, how
fast, how many errors" in aggregate.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from atp_platform.logging import get_logger
from atp_platform.metrics import counter, histogram

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_REQUESTS_TOTAL = counter(
    "atp_api_http_requests_total",
    "HTTP requests completed, by method and status code.",
    labelnames=("method", "status_code"),
)
_REQUEST_DURATION_SECONDS = histogram(
    "atp_api_http_request_duration_seconds",
    "HTTP request duration in seconds, by method.",
    labelnames=("method",),
)


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

        method = scope.get("method")
        status_code = status_code_holder["status_code"]
        duration_seconds = time.monotonic() - started_at

        self._logger.info(
            "http_request",
            method=method,
            path=scope.get("path"),
            status_code=status_code,
            duration_ms=round(duration_seconds * 1000, 2),
        )
        _REQUESTS_TOTAL.labels(method=method, status_code=str(status_code)).inc()
        _REQUEST_DURATION_SECONDS.labels(method=method).observe(duration_seconds)
