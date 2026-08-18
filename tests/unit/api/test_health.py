"""HEALTH: /healthz, /readyz, dependency-failure behavior, no topology
leakage."""

from __future__ import annotations

from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_api.config import ApiSettings
from atp_platform.config import Settings


def test_healthz_is_ok_and_deterministic(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    first = client.get("/healthz")
    second = client.get("/healthz")

    assert first.status_code == 200
    assert first.json() == {"status": "OK"}
    assert first.json() == second.json()


def test_healthz_never_mentions_database_or_redis(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    body = client.get("/healthz").text.lower()

    assert "database" not in body
    assert "redis" not in body
    assert "postgres" not in body


def test_readyz_fails_closed_when_database_is_unreachable(settings: Settings) -> None:
    """`settings.database_url` (the shared fixture) points at an
    unreachable port - proves dependency-failure behavior without Docker."""
    client = TestClient(create_app(settings=settings))
    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "FAIL"


def test_readyz_never_leaks_dsn_host_username_or_password(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    body = client.get("/readyz").text

    assert "baduser" not in body
    assert "badpass" not in body
    assert "127.0.0.1" not in body
    assert "postgresql" not in body.lower()


def test_readyz_never_leaks_a_stack_trace() -> None:
    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url="postgresql+psycopg://baduser:badpass@127.0.0.1:1/baddb",  # type: ignore[arg-type]
        redis_url="redis://:x@localhost:6379/0",  # type: ignore[arg-type]
    )
    client = TestClient(create_app(settings=settings, api_settings=ApiSettings()))
    body = client.get("/readyz").text

    assert "Traceback" not in body
    assert '  File "' not in body


def test_readyz_reason_is_a_fixed_opaque_string_not_the_raw_exception(settings: Settings) -> None:
    response = TestClient(create_app(settings=settings)).get("/readyz")
    assert response.json()["reason"] == "dependency_unavailable"
