"""Phase 1 Step 7: the audit and kill-switch read endpoints, and
/readyz and /api/v1/system/status's "healthy" path, against a real,
migrated database.

Docker-gated exactly like every other file in this directory - skips via
the shared conftest.py fixtures when TEST_DATABASE_URL is unset.
"""

from __future__ import annotations

import uuid

import psycopg
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from atp_api.app import create_app
from atp_api.config import ApiSettings
from atp_persistence.db import make_session_factory
from atp_platform.config import Settings


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
    return TestClient(app)


def test_readyz_reports_ok_against_a_reachable_database(
    migrated_database: str, owner_dsn: str
) -> None:
    client = _build_client(owner_dsn)
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "OK", "reason": None}


def test_system_status_reports_not_degraded_and_a_migration_version(
    migrated_database: str, owner_dsn: str
) -> None:
    client = _build_client(owner_dsn)
    response = client.get("/api/v1/system/status")

    body = response.json()
    assert response.status_code == 200
    assert body["degraded"] is False
    assert body["migration_version"] == "0003_table_grants"
    assert body["mode"] == "PAPER"


def test_kill_switches_endpoint_returns_the_four_seeded_switches(
    migrated_database: str, owner_dsn: str
) -> None:
    client = _build_client(owner_dsn)
    response = client.get("/api/v1/kill-switches")

    assert response.status_code == 200
    items = response.json()["items"]
    switch_ids = {item["switch_id"] for item in items}
    assert switch_ids == {"GLOBAL_LIVE", "LIVE_ACCOUNT", "PAPER", "API_EXECUTION"}

    by_id = {item["switch_id"]: item for item in items}
    assert by_id["GLOBAL_LIVE"]["engaged"] is True
    assert by_id["PAPER"]["engaged"] is False


def test_audit_events_endpoint_returns_a_seeded_event(
    migrated_database: str, owner_dsn: str, owner_connection: psycopg.Connection
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

    client = _build_client(owner_dsn)
    response = client.get("/api/v1/audit/events", params={"action": "TEST_EVENT", "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert any(item["event_id"] == event_id for item in body["items"])
    assert body["limit"] == 10

    with owner_connection.cursor() as cur:
        cur.execute("DELETE FROM audit.audit_events WHERE event_id = %s", (event_id,))
    owner_connection.commit()


def test_audit_events_endpoint_never_exposes_a_payload_field(
    migrated_database: str, owner_dsn: str
) -> None:
    client = _build_client(owner_dsn)
    response = client.get("/api/v1/audit/events")

    assert response.status_code == 200
    for item in response.json()["items"]:
        assert "payload" not in item
