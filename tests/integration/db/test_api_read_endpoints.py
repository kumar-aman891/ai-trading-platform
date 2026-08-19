"""Phase 1 Step 7: the audit and kill-switch read endpoints, and
/readyz and /api/v1/system/status's "healthy" path, against a real,
migrated database.

Docker-gated exactly like every other file in this directory - skips via
the shared conftest.py fixtures when TEST_DATABASE_URL is unset.

Every route here except /readyz is permission-gated (Phase 1 Step 8 RBAC,
tests/safety/test_rbac_server_side.py's "no route lacks explicit
permission" invariant) - so, like test_auth_flows.py, this file logs in
as a real administrator user before calling them. First real run against
Postgres (Phase 1 Step 12 Phase A) found these tests predated Step 8's
authentication requirement and had never been updated to log in, so every
call here used to return 401 rather than exercising the route at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from atp_api.app import create_app
from atp_api.config import ApiSettings
from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_ADMINISTRATOR
from atp_persistence.db import make_session_factory
from atp_platform.config import Settings
from tests.integration.db.conftest import delete_user_cascade


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_async_psycopg_url(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


def _build_client(owner_dsn: str) -> TestClient:
    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url=_as_async_psycopg_url(owner_dsn),  # type: ignore[arg-type]
        redis_url="redis://:x@localhost:6379/0",  # type: ignore[arg-type]
    )
    engine = create_async_engine(_as_async_psycopg_url(owner_dsn))
    session_factory = make_session_factory(engine)
    app = create_app(settings=settings, api_settings=ApiSettings(), session_factory=session_factory)
    # `client=` overrides Starlette's default ("testclient", 50000). The
    # login route records `request.client.host` into
    # `core.sessions.ip_address`, which is a Postgres `INET` column - the
    # literal string "testclient" is not a valid inet value, so the
    # default makes every real-database login fail with `DataError` ->
    # 503. Only surfaced on the first real run against Postgres (Phase 1
    # Step 12 Phase A); against the in-memory fakes in tests/unit/api/
    # there is no INET column to reject it.
    return TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50000))


def _insert_admin_user(
    owner_connection: psycopg.Connection, *, username: str, password: str
) -> str:
    user_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.users
                (user_id, username, password_hash, role, is_active, must_change_password,
                 created_at, updated_at)
            VALUES (%s, %s, %s, %s, true, false, now(), now())
            """,
            (user_id, username, hash_password(password), ROLE_ADMINISTRATOR),
        )
    owner_connection.commit()
    return user_id


@pytest.fixture
def authenticated_client(
    owner_dsn: str, owner_connection: psycopg.Connection
) -> Iterator[TestClient]:
    """An administrator-authenticated client - every route in this file
    except /readyz requires a real session (Phase 1 Step 8 RBAC)."""
    password = "a real password"
    user_id = _insert_admin_user(
        owner_connection, username=f"itest-read-{_new_uuid()}", password=password
    )
    try:
        client = _build_client(owner_dsn)
        with owner_connection.cursor() as cur:
            cur.execute("SELECT username FROM core.users WHERE user_id = %s", (user_id,))
            (username,) = cur.fetchone()  # type: ignore[misc]
        login = client.post("/api/v1/auth/login", json={"username": username, "password": password})
        assert login.status_code == 200, login.text
        yield client
    finally:
        delete_user_cascade(owner_connection, user_id)


def test_readyz_reports_ok_against_a_reachable_database(
    migrated_database: str, owner_dsn: str
) -> None:
    client = _build_client(owner_dsn)
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "OK", "reason": None}


def test_system_status_reports_not_degraded_and_a_migration_version(
    migrated_database: str, authenticated_client: TestClient
) -> None:
    response = authenticated_client.get("/api/v1/system/status")

    body = response.json()
    assert response.status_code == 200
    assert body["degraded"] is False
    assert body["migration_version"] == "0004_paper_cash_ledger_seed"
    assert body["mode"] == "PAPER"


def test_kill_switches_endpoint_returns_the_four_seeded_switches(
    migrated_database: str, authenticated_client: TestClient
) -> None:
    response = authenticated_client.get("/api/v1/kill-switches")

    assert response.status_code == 200
    items = response.json()["items"]
    switch_ids = {item["switch_id"] for item in items}
    assert switch_ids == {"GLOBAL_LIVE", "LIVE_ACCOUNT", "PAPER", "API_EXECUTION"}

    by_id = {item["switch_id"]: item for item in items}
    assert by_id["GLOBAL_LIVE"]["engaged"] is True
    assert by_id["PAPER"]["engaged"] is False


def test_audit_events_endpoint_returns_a_seeded_event(
    migrated_database: str,
    owner_connection: psycopg.Connection,
    authenticated_client: TestClient,
) -> None:
    event_id = _new_uuid()
    correlation_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO audit.audit_events
                (event_id, correlation_id, occurred_at, recorded_at, actor_type, action, mode)
            VALUES (%s, %s, now(), now(), 'SYSTEM', 'TEST_EVENT', 'PAPER')
            """,
            (event_id, correlation_id),
        )
    owner_connection.commit()

    # The seeded event is deliberately NOT cleaned up: `audit.audit_events`
    # is append-only (ADR-010), enforced by the `audit_events_append_only`
    # trigger, which rejects DELETE even for `atp_owner`. An earlier
    # version of this test attempted exactly that DELETE and only got away
    # with it because the request above used to 401 before ever reaching
    # the cleanup - the first real run against Postgres (Phase 1 Step 12
    # Phase A) surfaced `RaiseException: audit.audit_events is append-only:
    # DELETE is not permitted`. The row is harmless: it carries a unique
    # `event_id` and a `TEST_EVENT` action no other test asserts on, and
    # the whole database is a tmpfs-backed ephemeral stack destroyed at the
    # end of the run.
    response = authenticated_client.get(
        "/api/v1/audit/events", params={"action": "TEST_EVENT", "limit": 10}
    )

    assert response.status_code == 200
    body = response.json()
    assert any(item["event_id"] == event_id for item in body["items"])
    assert body["limit"] == 10


def test_audit_events_endpoint_never_exposes_a_payload_field(
    migrated_database: str, authenticated_client: TestClient
) -> None:
    response = authenticated_client.get("/api/v1/audit/events")

    assert response.status_code == 200
    for item in response.json()["items"]:
        assert "payload" not in item
