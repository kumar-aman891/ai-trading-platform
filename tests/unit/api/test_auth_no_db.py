"""AUTHENTICATION / ERRORS: paths that are meaningful to test against the
*real* (unreachable) database dependency, i.e. without
`tests/unit/api/conftest.py`'s fake-`UnitOfWork` override - proves the
Step 7 "fail closed, never leak connection details" behavior still holds
for the new Step 8 auth routes, and that a validation failure never echoes
a submitted password back to the caller.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_platform.config import Settings


def test_login_against_an_unreachable_database_fails_closed(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    response = client.post(
        "/api/v1/auth/login", json={"username": "someone", "password": "whatever-password"}
    )
    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"


def test_login_failure_response_never_leaks_dsn_host_or_password(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    response = client.post(
        "/api/v1/auth/login", json={"username": "someone", "password": "super-secret-password"}
    )
    body = response.text
    assert "super-secret-password" not in body
    assert "baduser" not in body
    assert "badpass" not in body
    assert "127.0.0.1" not in body


def test_login_validation_error_never_echoes_the_submitted_password(settings: Settings) -> None:
    """`LoginRequest.password` has `min_length=1` - an empty-string
    password fails Pydantic validation *before* any dependency runs, and
    the 422 response must not echo it back (atp_api.errors's stripped
    "input" key)."""
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    response = client.post("/api/v1/auth/login", json={"username": "someone", "password": ""})
    assert response.status_code == 422
    for error in response.json()["errors"]:
        assert "input" not in error


def test_login_validation_error_on_an_oversized_field_does_not_echo_it(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    oversized_password = "x" * 2000
    response = client.post(
        "/api/v1/auth/login", json={"username": "someone", "password": oversized_password}
    )
    assert response.status_code == 422
    assert oversized_password not in response.text


def test_logout_with_a_cookie_against_an_unreachable_database_fails_closed(
    settings: Settings,
) -> None:
    """A cookie-less logout is a pure no-op that never touches the
    database (see `atp_api.services.auth.logout`'s short-circuit) - a
    cookie must be present to exercise the DB-dependent path here."""
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    client.cookies.set("atp_session", "some-cookie-value")
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 503


def test_logout_without_a_cookie_never_touches_the_database(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200


def test_me_against_an_unreachable_database_fails_closed(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings), base_url="https://testserver")
    client.cookies.set("atp_session", "some-cookie-value")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 503
