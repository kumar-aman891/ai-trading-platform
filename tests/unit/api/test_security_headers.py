"""SECURITY HEADERS: expected headers present, HSTS only when configured
appropriately (`Settings.environment == "production"`)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_platform.config import Settings


def _settings(**overrides: object) -> Settings:
    base = {
        "session_secret_key": "a" * 40,
        "database_url": "postgresql+psycopg://baduser:badpass@127.0.0.1:1/baddb",
        "redis_url": "redis://:x@localhost:6379/0",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_security_headers_present_on_every_response() -> None:
    client = TestClient(create_app(settings=_settings()))
    response = client.get("/healthz")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_hsts_absent_in_development(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    response = client.get("/healthz")

    assert "strict-transport-security" not in response.headers


def test_hsts_present_in_production() -> None:
    client = TestClient(create_app(settings=_settings(environment="production")))
    response = client.get("/healthz")

    assert "max-age" in response.headers["strict-transport-security"]


def test_security_headers_present_on_error_responses() -> None:
    client = TestClient(create_app(settings=_settings()))
    response = client.get("/api/v1/does-not-exist")

    assert response.headers.get("x-content-type-options") == "nosniff"
