"""Shared fixtures for Phase 1 Step 7's application-layer tests.

None of these tests require Docker or a reachable database: `Settings`
accepts `UNREACHABLE_DATABASE_URL` (a syntactically valid DSN pointing at
a port nothing listens on, so connection attempts fail fast rather than
hanging) and `atp_api.app.create_app` never connects at construction time
(SQLAlchemy's `create_async_engine` is lazy) - see that module's
docstring. Tests that need a genuinely *successful* database round trip
live in `tests/integration/db/` instead, skip-gated exactly like every
other Docker-dependent test in this repository.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_api.config import ApiSettings
from atp_api.deps import get_clock, get_id_generator, get_unit_of_work
from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_platform.config import Settings
from tests.unit.api.fakes import FakeUnitOfWork

VALID_SECRET = "a" * 40
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://baduser:badpass@127.0.0.1:1/baddb"
VALID_REDIS_URL = "redis://:fixture-only@localhost:6379/0"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        session_secret_key=VALID_SECRET,  # type: ignore[arg-type]
        database_url=UNREACHABLE_DATABASE_URL,  # type: ignore[arg-type]
        redis_url=VALID_REDIS_URL,  # type: ignore[arg-type]
    )


@pytest.fixture
def api_settings() -> ApiSettings:
    return ApiSettings(cors_allowed_origins=("http://allowed.example",))


@pytest.fixture
def fake_uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def frozen_clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture
def id_generator() -> SequentialIdGenerator:
    return SequentialIdGenerator()


@pytest.fixture
def app(settings: Settings, api_settings: ApiSettings, fake_uow, frozen_clock, id_generator):
    """A fully-wired app whose *database* dependency (`get_unit_of_work`) is
    swapped for an in-memory fake (`tests/unit/api/fakes.py`) - lets Step 8's
    login/session/RBAC/CSRF logic be exercised through real HTTP requests
    without Docker. `get_db_session` (the Step 7 read-only dependency used
    by kill-switches/audit) is deliberately left un-overridden, pointed at
    the real (unreachable) DSN - see test_auth_flows.py's module docstring
    for why that is still a meaningful assertion, not a gap."""
    application = create_app(settings=settings, api_settings=api_settings)

    async def _override_uow():
        yield fake_uow

    application.dependency_overrides[get_unit_of_work] = _override_uow
    application.dependency_overrides[get_clock] = lambda: frozen_clock
    application.dependency_overrides[get_id_generator] = lambda: id_generator
    return application


@pytest.fixture
def client(app) -> TestClient:
    # https:// base URL so `Secure`-flagged cookies (atp_api.security.cookies)
    # actually round-trip through httpx's cookie jar between requests -
    # required for any test that logs in and then reuses the cookie.
    return TestClient(app, base_url="https://testserver")
