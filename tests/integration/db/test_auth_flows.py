"""Phase 1 Step 8: authentication/session/RBAC/bootstrap against a real,
migrated database. Docker-gated exactly like every other file in this
directory - skips via the shared conftest.py fixtures when
`TEST_DATABASE_URL` is unset.

`tests/unit/api/test_auth_flows.py` already exercises this logic
extensively against an in-memory fake; this file's job is narrower: prove
the real `SqlAlchemyUserRepository`/`SqlAlchemySessionRepository`/
`SqlAlchemyAuditEventWriter` round-trip through actual PostgreSQL tables
and grants, including the one thing the fake cannot exercise at all - a
row genuinely already expired/revoked at the database level.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from atp_api.app import create_app
from atp_api.bootstrap import bootstrap_admin
from atp_api.config import ApiSettings
from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_ADMINISTRATOR, ROLE_VIEWER
from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_persistence.db import make_session_factory, unit_of_work
from atp_platform.config import Settings


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_async_psycopg_url(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


def _build_client(owner_dsn: str, *, bootstrap_admin_token: str | None = None) -> TestClient:
    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url=_as_async_psycopg_url(owner_dsn),  # type: ignore[arg-type]
        redis_url="redis://:x@localhost:6379/0",  # type: ignore[arg-type]
    )
    engine = create_async_engine(_as_async_psycopg_url(owner_dsn))
    session_factory = make_session_factory(engine)
    api_settings = ApiSettings(bootstrap_admin_token=bootstrap_admin_token)  # type: ignore[arg-type]
    app = create_app(settings=settings, api_settings=api_settings, session_factory=session_factory)
    return TestClient(app, base_url="https://testserver")


def _insert_user(
    owner_connection: psycopg.Connection, *, username: str, password: str, role: str
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
            (user_id, username, hash_password(password), role),
        )
    owner_connection.commit()
    return user_id


def _delete_user_cascade(owner_connection: psycopg.Connection, user_id: str) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("DELETE FROM core.sessions WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM core.users WHERE user_id = %s", (user_id,))
    owner_connection.commit()


def test_login_then_me_then_logout_round_trips_through_real_tables(
    migrated_database: str, owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    user_id = _insert_user(
        owner_connection,
        username=f"itest-{_new_uuid()}",
        password="a real password",
        role=ROLE_VIEWER,
    )
    try:
        client = _build_client(owner_dsn)
        login = client.post(
            "/api/v1/auth/login",
            json={
                "username": _get_username(owner_connection, user_id),
                "password": "a real password",
            },
        )
        assert login.status_code == 200

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == ROLE_VIEWER

        csrf_cookie = client.cookies.get("atp_csrf")
        logout = client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_cookie})
        assert logout.status_code == 200

        after_logout = client.get("/api/v1/auth/me")
        assert after_logout.status_code == 401
    finally:
        _delete_user_cascade(owner_connection, user_id)


def _get_username(owner_connection: psycopg.Connection, user_id: str) -> str:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT username FROM core.users WHERE user_id = %s", (user_id,))
        row = cur.fetchone()
        assert row is not None
        return str(row[0])


def test_a_session_already_expired_in_the_database_is_rejected(
    migrated_database: str, owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    user_id = _insert_user(
        owner_connection,
        username=f"itest-expired-{_new_uuid()}",
        password="a real password",
        role=ROLE_VIEWER,
    )
    try:
        client = _build_client(owner_dsn)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": _get_username(owner_connection, user_id),
                "password": "a real password",
            },
        )
        # Directly force the stored session into the past - something the
        # in-memory fake in tests/unit/api/ can only simulate via a
        # FrozenClock, never a genuinely already-expired database row.
        with owner_connection.cursor() as cur:
            cur.execute(
                "UPDATE core.sessions SET expires_at = %s WHERE user_id = %s",
                (datetime.now(UTC) - timedelta(hours=1), user_id),
            )
        owner_connection.commit()

        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert response.json()["code"] == "SESSION_EXPIRED"
    finally:
        _delete_user_cascade(owner_connection, user_id)


def test_administrator_role_reaches_kill_switches_and_audit_against_a_real_database(
    migrated_database: str, owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    user_id = _insert_user(
        owner_connection,
        username=f"itest-admin-{_new_uuid()}",
        password="a real password",
        role=ROLE_ADMINISTRATOR,
    )
    try:
        client = _build_client(owner_dsn)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": _get_username(owner_connection, user_id),
                "password": "a real password",
            },
        )
        assert client.get("/api/v1/kill-switches").status_code == 200
        assert client.get("/api/v1/audit/events").status_code == 200
    finally:
        _delete_user_cascade(owner_connection, user_id)


def test_viewer_role_is_forbidden_from_kill_switches_against_a_real_database(
    migrated_database: str, owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    user_id = _insert_user(
        owner_connection,
        username=f"itest-viewer-{_new_uuid()}",
        password="a real password",
        role=ROLE_VIEWER,
    )
    try:
        client = _build_client(owner_dsn)
        client.post(
            "/api/v1/auth/login",
            json={
                "username": _get_username(owner_connection, user_id),
                "password": "a real password",
            },
        )
        response = client.get("/api/v1/kill-switches")
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"
    finally:
        _delete_user_cascade(owner_connection, user_id)


def test_bootstrap_admin_creates_the_first_administrator(
    migrated_database: str, owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM core.users")
        (existing,) = cur.fetchone()  # type: ignore[misc]
    if existing:
        pytest.skip(
            "core.users already has rows in this shared test database - "
            "not this test's job to clear it"
        )

    engine = create_async_engine(_as_async_psycopg_url(owner_dsn))
    session_factory = make_session_factory(engine)

    async def run() -> str:
        async with unit_of_work(session_factory) as uow:
            return await bootstrap_admin(
                uow,
                provided_token="test-token",
                expected_token="test-token",
                username="bootstrap-admin",
                password="a strong bootstrap password",
                clock=UTCClock(),
                id_generator=UUIDv7Generator(),
                correlation_id=str(uuid.uuid4()),
            )

    try:
        user_id = asyncio.run(run())
        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT role, must_change_password FROM core.users WHERE user_id = %s", (user_id,)
            )
            role, must_change_password = cur.fetchone()  # type: ignore[misc]
        assert role == ROLE_ADMINISTRATOR
        assert must_change_password is True
    finally:
        asyncio.run(engine.dispose())
        _delete_user_cascade(owner_connection, user_id)
