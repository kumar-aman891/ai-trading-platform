"""CORRELATION: generated when absent, preserved when supplied, appears
in the response header and in error bodies. The middleware itself
(`atp_platform.asgi.CorrelationIdMiddleware`) already has its own unit
tests (tests/unit/test_asgi_middleware.py) - these tests prove it is
actually wired into `atp_api.app.create_app`, end to end."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_platform.config import Settings


def test_correlation_id_is_generated_when_absent(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    response = client.get("/healthz")

    correlation_id = response.headers.get("x-correlation-id")
    assert correlation_id is not None
    assert uuid.UUID(correlation_id)  # generated one is a valid UUID


def test_correlation_id_is_preserved_when_supplied(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    response = client.get("/healthz", headers={"X-Correlation-ID": "caller-supplied-value"})

    assert response.headers["x-correlation-id"] == "caller-supplied-value"


def test_two_requests_get_two_different_generated_correlation_ids(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    first = client.get("/healthz").headers["x-correlation-id"]
    second = client.get("/healthz").headers["x-correlation-id"]

    assert first != second
