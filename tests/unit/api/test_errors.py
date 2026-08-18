"""ERRORS: stable error schema, correlation ID returned, no traceback/DSN
leakage."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_api.errors import ApiError, NotFoundError, register_exception_handlers
from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_RESEARCHER
from atp_persistence.repositories import UserRecord
from atp_platform.config import Settings


def test_unknown_route_returns_404_with_stable_shape(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert "detail" in body or "code" in body  # FastAPI's own 404 vs. our handler


def test_api_error_maps_to_its_declared_status_and_code(settings: Settings) -> None:
    app = create_app(settings=settings)

    @app.get("/__test/not-found")
    async def _raise_not_found() -> None:
        raise NotFoundError("no such thing")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__test/not-found")

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"] == "no such thing"


def test_error_response_includes_the_request_correlation_id(settings: Settings) -> None:
    app = create_app(settings=settings)

    @app.get("/__test/not-found")
    async def _raise_not_found() -> None:
        raise NotFoundError()

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__test/not-found", headers={"X-Correlation-ID": "fixed-id-123"})

    assert response.json()["correlation_id"] == "fixed-id-123"
    assert response.headers["x-correlation-id"] == "fixed-id-123"


def test_unexpected_exception_returns_generic_500_with_no_traceback(settings: Settings) -> None:
    app = create_app(settings=settings)

    @app.get("/__test/boom")
    async def _raise_boom() -> None:
        raise RuntimeError("sensitive internal detail: db=prod-primary")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__test/boom")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "INTERNAL_ERROR"
    assert "sensitive internal detail" not in body["message"]
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text


def test_domain_error_maps_to_422(settings: Settings) -> None:
    from atp_domain.errors import InvalidMoneyValueError

    app = create_app(settings=settings)

    @app.get("/__test/domain-error")
    async def _raise_domain_error() -> None:
        raise InvalidMoneyValueError("Quantity must not be negative.")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__test/domain-error")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "DOMAIN_VALIDATION_ERROR"
    assert "Quantity must not be negative." in body["message"]


def test_request_validation_error_returns_422(client, fake_uow) -> None:
    """`/api/v1/audit/events` requires authentication (`READ_AUDIT`) as of
    Phase 1 Step 8, so this test logs in first via the fake `UnitOfWork`
    (`tests/unit/api/conftest.py`) before exercising the bounded `limit`
    query parameter (`le=200`)."""
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    fake_uow.users._by_id["user-1"] = UserRecord(
        user_id="user-1",
        username="erroruser",
        password_hash=hash_password("correct horse battery staple"),
        role=ROLE_RESEARCHER,
        is_active=True,
        must_change_password=False,
        created_at=created_at,
        updated_at=created_at,
    )
    client.post(
        "/api/v1/auth/login",
        json={"username": "erroruser", "password": "correct horse battery staple"},
    )

    response = client.get("/api/v1/audit/events", params={"limit": 999999})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "REQUEST_VALIDATION_ERROR"
    assert "correlation_id" in body


def test_error_response_never_contains_sql_or_dsn(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    response = client.get("/api/v1/audit/events")

    body = response.text.lower()
    assert "select " not in body
    assert "postgresql://" not in body
    assert "baduser" not in body


def test_register_exception_handlers_is_idempotent_on_a_fresh_app() -> None:
    app = FastAPI()
    register_exception_handlers(app)
    assert ApiError in app.exception_handlers
