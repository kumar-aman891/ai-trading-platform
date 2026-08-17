"""Tests for atp_platform.asgi.CorrelationIdMiddleware.

Driven with a hand-rolled ASGI scope/receive/send — no test-client or HTTP
library dependency, consistent with Phase 1's "no HTTP client dependencies"
invariant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from atp_platform.asgi import CorrelationIdMiddleware
from atp_platform.correlation import get_correlation_id

Scope = dict[str, Any]
Message = dict[str, Any]


def _make_http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {"type": "http", "method": "GET", "path": "/", "headers": headers or []}


async def _drive(app: Callable[[Scope, Any, Any], Awaitable[None]], scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def test_correlation_id_created_when_header_absent() -> None:
    seen: dict[str, str | None] = {}

    async def inner_app(scope: Scope, receive: Any, send: Any) -> None:
        seen["cid"] = get_correlation_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = CorrelationIdMiddleware(inner_app)
    sent = asyncio.run(_drive(middleware, _make_http_scope()))

    assert seen["cid"] is not None
    response_headers = dict(sent[0]["headers"])
    assert response_headers[b"x-correlation-id"].decode() == seen["cid"]


def test_supplied_correlation_id_header_is_preserved() -> None:
    seen: dict[str, str | None] = {}

    async def inner_app(scope: Scope, receive: Any, send: Any) -> None:
        seen["cid"] = get_correlation_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})

    middleware = CorrelationIdMiddleware(inner_app)
    # An unrelated header precedes the target one, so header scanning must
    # skip past a non-matching entry before finding the match.
    scope = _make_http_scope(
        headers=[
            (b"content-type", b"text/plain"),
            (b"x-correlation-id", b"caller-supplied-id"),
        ]
    )
    asyncio.run(_drive(middleware, scope))

    assert seen["cid"] == "caller-supplied-id"


def test_correlation_id_is_reset_after_the_request() -> None:
    async def inner_app(scope: Scope, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})

    assert get_correlation_id() is None
    middleware = CorrelationIdMiddleware(inner_app)
    asyncio.run(_drive(middleware, _make_http_scope()))
    assert get_correlation_id() is None


def test_non_http_scope_passes_through_without_binding_correlation_id() -> None:
    seen: dict[str, str | None] = {"cid": "unset"}

    async def inner_app(scope: Scope, receive: Any, send: Any) -> None:
        seen["cid"] = get_correlation_id()

    middleware = CorrelationIdMiddleware(inner_app)
    asyncio.run(_drive(middleware, {"type": "lifespan"}))

    assert seen["cid"] is None
