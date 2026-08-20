"""Safety invariant (Phase 1 Step 14, ADR-007): `GLOBAL_LIVE` and
`LIVE_ACCOUNT` have no mutation route in Phase 1, and never will while
ADR-007 stands ("No - no route exists to clear it"). Proven two ways,
mechanically:

1. Structural - `atp_domain.killswitch.MUTABLE_SWITCH_SCOPES`, the single
   source of truth `atp_api.services.kill_switches` consults before any
   write is attempted, excludes both scopes by construction.
2. Behavioral - a real HTTP request against the fully-wired app, as the
   *strongest* role in the system (`administrator`, who can do everything
   a weaker role can and more), against both switches and both verbs,
   is rejected with 403. If either check alone were ever satisfied while
   the other silently regressed - the constant edited without the route
   behavior following, or vice versa - this file would still catch it.

The behavioral half deliberately does not use `tests/unit/api/conftest.py`'s
fixtures - `pytest` scopes a directory's `conftest.py` to that directory's
subtree, so `tests/safety/` (a sibling, not a child, of `tests/unit/api/`)
cannot see them. It reuses that module's fakes/settings construction
directly instead, matching `tests/unit/api/test_health.py`'s bare
`TestClient(create_app(...))` pattern rather than depending on fixtures
this file has no access to.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_api.deps import get_clock, get_id_generator, get_instrument_repository, get_unit_of_work
from atp_api.security.cookies import CSRF_COOKIE_NAME
from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_ADMINISTRATOR
from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_domain.killswitch import MUTABLE_SWITCH_SCOPES, SwitchScope
from atp_persistence.repositories import UserRecord
from atp_platform.config import Settings
from tests.unit.api.fakes import FakeInstrumentRepository, FakeUnitOfWork

pytestmark = pytest.mark.safety

_UNREACHABLE_DATABASE_URL = "postgresql+psycopg://baduser:badpass@127.0.0.1:1/baddb"
_PASSWORD = "correct horse battery staple"


def test_mutable_switch_scopes_never_includes_global_live_or_live_account() -> None:
    assert SwitchScope.GLOBAL_LIVE not in MUTABLE_SWITCH_SCOPES
    assert SwitchScope.LIVE_ACCOUNT not in MUTABLE_SWITCH_SCOPES


@pytest.mark.parametrize("switch_id", ["GLOBAL_LIVE", "LIVE_ACCOUNT"])
@pytest.mark.parametrize("verb", ["engage", "disengage"])
def test_administrator_cannot_mutate_global_live_or_live_account_over_real_http(
    switch_id: str, verb: str
) -> None:
    """`administrator` holds every kill-switch permission this codebase
    defines (`ENGAGE_KILL_SWITCH` and `DISENGAGE_KILL_SWITCH` both) - if
    this role cannot reach the mutation, no role can. Runs the full app,
    a real login, and a real CSRF-protected POST - not a unit-level
    stand-in for the route."""
    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url=_UNREACHABLE_DATABASE_URL,  # type: ignore[arg-type]
        redis_url="redis://:fixture-only@localhost:6379/0",  # type: ignore[arg-type]
    )
    app = create_app(settings=settings)
    fake_uow = FakeUnitOfWork()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    fake_uow.users._by_id["admin-1"] = UserRecord(
        user_id="admin-1",
        username="safety-admin",
        password_hash=hash_password(_PASSWORD),
        role=ROLE_ADMINISTRATOR,
        is_active=True,
        must_change_password=False,
        created_at=created_at,
        updated_at=created_at,
    )

    async def _override_uow():
        yield fake_uow

    app.dependency_overrides[get_unit_of_work] = _override_uow
    app.dependency_overrides[get_clock] = lambda: FrozenClock(created_at)
    app.dependency_overrides[get_id_generator] = lambda: SequentialIdGenerator()
    app.dependency_overrides[get_instrument_repository] = lambda: FakeInstrumentRepository()

    client = TestClient(app, base_url="https://testserver")
    client.post("/api/v1/auth/login", json={"username": "safety-admin", "password": _PASSWORD})
    csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)

    response = client.post(
        f"/api/v1/kill-switches/{switch_id}/{verb}",
        json={"reason": "safety-test attempt"},
        headers={"x-csrf-token": csrf_cookie} if csrf_cookie else {},
    )

    assert response.status_code == 403
    assert fake_uow.kill_switches.apply_transition_calls == []
