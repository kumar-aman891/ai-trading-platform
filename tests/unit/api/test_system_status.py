"""SYSTEM STATUS: returns PAPER, cannot be overridden through request
input, no LIVE value can be supplied through query/header/body.

`GET /api/v1/system/status` requires authentication as of Phase 1 Step 8
(any role - `READ_SYSTEM` is granted to all five), so every test here
logs in first via the fake in-memory `UnitOfWork`
(`tests/unit/api/conftest.py`'s `client`/`fake_uow` fixtures) before
hitting the route.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_VIEWER
from atp_persistence.repositories import UserRecord
from tests.unit.api.fakes import FakeUnitOfWork

STATUS_PATH = "/api/v1/system/status"
_PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hash_password(_PASSWORD)


def _login(client: TestClient, fake_uow: FakeUnitOfWork) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    fake_uow.users._by_id["user-1"] = UserRecord(
        user_id="user-1",
        username="statususer",
        password_hash=_PASSWORD_HASH,
        role=ROLE_VIEWER,
        is_active=True,
        must_change_password=False,
        created_at=created_at,
        updated_at=created_at,
    )
    response = client.post(
        "/api/v1/auth/login", json={"username": "statususer", "password": _PASSWORD}
    )
    assert response.status_code == 200


def test_system_status_requires_authentication(client: TestClient) -> None:
    response = client.get(STATUS_PATH)
    assert response.status_code == 401


def test_system_status_reports_paper_mode(client: TestClient, fake_uow: FakeUnitOfWork) -> None:
    _login(client, fake_uow)
    response = client.get(STATUS_PATH)

    assert response.status_code == 200
    assert response.json()["mode"] == "PAPER"


def test_system_status_ignores_a_mode_query_parameter(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _login(client, fake_uow)
    response = client.get(STATUS_PATH, params={"mode": "LIVE"})

    assert response.status_code == 200
    assert response.json()["mode"] == "PAPER"


def test_system_status_ignores_a_mode_header(client: TestClient, fake_uow: FakeUnitOfWork) -> None:
    _login(client, fake_uow)
    response = client.get(STATUS_PATH, headers={"X-Mode": "LIVE", "Mode": "LIVE"})

    assert response.status_code == 200
    assert response.json()["mode"] == "PAPER"


def test_system_status_route_accepts_no_request_body(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    """A GET route with no Pydantic request-body parameter cannot be
    tricked into reading a `mode` field from a JSON body at all - proven
    by sending one and confirming it has zero effect."""
    _login(client, fake_uow)
    response = client.request("GET", STATUS_PATH, json={"mode": "LIVE"})

    assert response.status_code == 200
    assert response.json()["mode"] == "PAPER"


def test_system_status_reports_degraded_when_database_is_unreachable(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _login(client, fake_uow)
    response = client.get(STATUS_PATH)

    body = response.json()
    assert body["degraded"] is True
    assert body["migration_version"] is None
    assert {dep["name"] for dep in body["dependencies"]} == {"database"}
    assert all(dep["status"] == "FAIL" for dep in body["dependencies"])


def test_system_status_never_exposes_dsn_or_credentials(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _login(client, fake_uow)
    body = client.get(STATUS_PATH).text

    assert "baduser" not in body
    assert "badpass" not in body


def test_system_status_reports_the_configured_environment(
    client: TestClient, fake_uow: FakeUnitOfWork, settings
) -> None:
    _login(client, fake_uow)
    response = client.get(STATUS_PATH)

    assert response.json()["environment"] == settings.environment
