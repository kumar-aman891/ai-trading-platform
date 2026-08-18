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
from atp_api.deps import (
    get_cash_ledger_repository,
    get_clock,
    get_fill_repository,
    get_id_generator,
    get_instrument_repository,
    get_order_repository,
    get_position_repository,
    get_risk_decision_repository,
    get_trade_proposal_repository,
    get_unit_of_work,
)
from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_platform.config import Settings
from tests.unit.api.fakes import (
    FakeCashLedgerRepository,
    FakeFillRepository,
    FakeInstrumentRepository,
    FakeOrderRepository,
    FakePositionRepository,
    FakeRiskDecisionRepository,
    FakeUnitOfWork,
)

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
def fake_instrument_repository() -> FakeInstrumentRepository:
    return FakeInstrumentRepository()


@pytest.fixture
def fake_risk_decision_repository() -> FakeRiskDecisionRepository:
    return FakeRiskDecisionRepository()


@pytest.fixture
def fake_order_repository() -> FakeOrderRepository:
    return FakeOrderRepository()


@pytest.fixture
def fake_fill_repository() -> FakeFillRepository:
    return FakeFillRepository()


@pytest.fixture
def fake_position_repository() -> FakePositionRepository:
    return FakePositionRepository()


@pytest.fixture
def fake_cash_ledger_repository() -> FakeCashLedgerRepository:
    return FakeCashLedgerRepository()


@pytest.fixture
def app(
    settings: Settings,
    api_settings: ApiSettings,
    fake_uow,
    frozen_clock,
    id_generator,
    fake_instrument_repository,
    fake_risk_decision_repository,
    fake_order_repository,
    fake_fill_repository,
    fake_position_repository,
    fake_cash_ledger_repository,
):
    """A fully-wired app whose *database* dependency (`get_unit_of_work`) is
    swapped for an in-memory fake (`tests/unit/api/fakes.py`) - lets Step 8's
    login/session/RBAC/CSRF logic be exercised through real HTTP requests
    without Docker. `get_db_session` (the Step 7 read-only dependency used
    by kill-switches/audit) is deliberately left un-overridden, pointed at
    the real (unreachable) DSN - see test_auth_flows.py's module docstring
    for why that is still a meaningful assertion, not a gap.

    Phase 1 Step 10's read-repository dependencies (`get_instrument_repository`,
    `get_risk_decision_repository`, `get_order_repository`, `get_fill_repository`,
    `get_position_repository`, `get_cash_ledger_repository`) are overridden
    individually with in-memory fakes instead, so `atp_api.routers.paper`/
    `instruments` can be genuinely exercised without Docker too.
    `get_trade_proposal_repository` is overridden with `fake_uow.trade_proposals`
    itself (not a separate fake) - the same object the POST intake route
    writes through - so a proposal submitted in a test is immediately
    visible to a subsequent read in the same test, matching real
    PostgreSQL's read-your-own-write behavior across `atp_api`'s two
    session types."""
    application = create_app(settings=settings, api_settings=api_settings)

    async def _override_uow():
        yield fake_uow

    application.dependency_overrides[get_unit_of_work] = _override_uow
    application.dependency_overrides[get_clock] = lambda: frozen_clock
    application.dependency_overrides[get_id_generator] = lambda: id_generator
    application.dependency_overrides[get_instrument_repository] = lambda: fake_instrument_repository
    application.dependency_overrides[get_trade_proposal_repository] = (
        lambda: fake_uow.trade_proposals
    )
    application.dependency_overrides[get_risk_decision_repository] = (
        lambda: fake_risk_decision_repository
    )
    application.dependency_overrides[get_order_repository] = lambda: fake_order_repository
    application.dependency_overrides[get_fill_repository] = lambda: fake_fill_repository
    application.dependency_overrides[get_position_repository] = lambda: fake_position_repository
    application.dependency_overrides[get_cash_ledger_repository] = (
        lambda: fake_cash_ledger_repository
    )
    return application


@pytest.fixture
def client(app) -> TestClient:
    # https:// base URL so `Secure`-flagged cookies (atp_api.security.cookies)
    # actually round-trip through httpx's cookie jar between requests -
    # required for any test that logs in and then reuses the cookie.
    return TestClient(app, base_url="https://testserver")
