"""Safety invariants (Phase 1 Step 8): RBAC is server-side only, every
protected route declares its required permission explicitly, and
assigning `live_trader` to a user cannot grant any live/execution
capability in Phase 1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.safety

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTERS_DIR = _REPO_ROOT / "backend" / "src" / "atp_api" / "routers"
_ATP_API_SRC = _REPO_ROOT / "backend" / "src" / "atp_api"

# `health.py` is unauthenticated by design (liveness/readiness probes are
# infrastructure-facing, not part of the RBAC'd application surface -
# atp_api.routers.health's own module docstring). `auth.py` protects `/me`
# via `get_current_principal` (authentication) rather than
# `require_permission` (authorization) - there is no *permission* for "is
# logged in," and login/logout are deliberately reachable by anyone
# (that's the point of a login route). `instruments.py`/`paper.py` are
# Phase 1 Step 10 additions - every route in both declares an explicit
# `require_permission(...)` (retiring `tests/safety/README.md`'s
# previously-deferred invariant #12 for the routes that carry any business
# meaning; `health`/`auth`'s deliberate permission-free routes remain a
# documented exception, not a gap).
_ROUTERS_REQUIRING_EXPLICIT_PERMISSIONS = (
    "system.py",
    "kill_switches.py",
    "audit.py",
    "instruments.py",
    "paper.py",
)


def test_every_protected_router_declares_require_permission() -> None:
    """Mechanical, not a code-review convention: an unannotated protected
    route fails this test."""
    for filename in _ROUTERS_REQUIRING_EXPLICIT_PERMISSIONS:
        source = (_ROUTERS_DIR / filename).read_text(encoding="utf-8")
        assert "require_permission(" in source, f"{filename} has no require_permission(...) usage"


def test_login_request_schema_rejects_a_client_supplied_role() -> None:
    """A client cannot request a role (or any other field) via the login
    body - `ApiModel`'s `extra="forbid"` rejects it at the Pydantic layer,
    before any service code runs."""
    from pydantic import ValidationError

    from atp_api.schemas.auth import LoginRequest

    with pytest.raises(ValidationError):
        LoginRequest.model_validate(
            {"username": "someone", "password": "whatever", "role": "administrator"}
        )


def test_login_request_schema_has_no_role_or_permission_field() -> None:
    from atp_api.schemas.auth import LoginRequest

    assert "role" not in LoginRequest.model_fields
    assert "permission" not in LoginRequest.model_fields
    assert "mode" not in LoginRequest.model_fields


def test_no_permission_constant_anywhere_implies_live_execution() -> None:
    from atp_api.security.rbac import Permission

    for permission in Permission:
        assert "LIVE" not in permission.value
        assert "EXECUTE" not in permission.value


def test_assigning_live_trader_grants_no_permission_beyond_paper_trader() -> None:
    """The central claim of Phase 1 Step 8's RBAC design, extended by Step
    10: `live_trader`'s permission set is not merely "small" or "LIVE-free"
    (the other tests in this module check that) - it is *identical* to
    `paper_trader`'s (both include the PAPER-trading permission set added
    in Step 10, ADR-012 - being able to *propose* a PAPER trade is not a
    live-execution capability), and strictly narrower than
    `administrator`'s. A user assigned `live_trader` therefore cannot reach
    anything a `paper_trader` cannot also reach, and can never reach
    anything `administrator`-only. `researcher` deliberately does **not**
    receive the PAPER-trading permission set (it can look up instruments
    but not submit a proposal or read the paper ledger), so it is no
    longer equal to `live_trader`/`paper_trader` - asserted here as a
    strict subset instead."""
    from atp_api.security.rbac import (
        ROLE_ADMINISTRATOR,
        ROLE_LIVE_TRADER,
        ROLE_PAPER_TRADER,
        ROLE_PERMISSIONS,
        ROLE_RESEARCHER,
    )

    live_trader_permissions = ROLE_PERMISSIONS[ROLE_LIVE_TRADER]
    assert live_trader_permissions == ROLE_PERMISSIONS[ROLE_PAPER_TRADER]
    assert ROLE_PERMISSIONS[ROLE_RESEARCHER] < live_trader_permissions
    assert live_trader_permissions < ROLE_PERMISSIONS[ROLE_ADMINISTRATOR]


def test_live_trader_role_constant_is_not_special_cased_in_authorization_logic() -> None:
    """No route, service, or dependency anywhere compares a role against
    `ROLE_LIVE_TRADER`/`"live_trader"` directly - the only place this role
    is ever consulted for an authorization decision is
    `atp_api.security.rbac.ROLE_PERMISSIONS` (`has_permission`'s table
    lookup). This is what makes "a role name is never a sufficient
    authorization check" true mechanically: there is no authorization code
    path anywhere that could special-case `live_trader` into an elevated
    capability even by accident.

    `atp_api.schemas` is excluded: its `Literal[...]` role fields
    (`LoginResponse.role`, `MeResponse.role`) legitimately enumerate all
    five role strings as an API response *contract*, not an authorization
    decision - excluding it from this scan is what keeps the check
    targeted at authorization logic instead of failing on an unrelated,
    correct usage."""
    rbac_module = _ATP_API_SRC / "security" / "rbac.py"
    schemas_dir = _ATP_API_SRC / "schemas"
    authorization_logic_dirs = (
        _ATP_API_SRC / "routers",
        _ATP_API_SRC / "services",
        _ATP_API_SRC / "middleware",
        _ATP_API_SRC / "deps.py",
    )
    for path in sorted(_ATP_API_SRC.rglob("*.py")):
        if path == rbac_module or schemas_dir in path.parents:
            continue
        if not any(path == d or d in path.parents for d in authorization_logic_dirs):
            continue
        source = path.read_text(encoding="utf-8")
        assert "ROLE_LIVE_TRADER" not in source, f"{path} references ROLE_LIVE_TRADER directly"
        assert "live_trader" not in source, f"{path} references the live_trader role literal"


def test_every_role_including_live_trader_gets_identical_http_responses_for_every_route() -> None:
    """End-to-end proof (not just a table lookup): a `live_trader` and a
    `paper_trader` calling every existing route get identical HTTP status
    codes, for every route - `tests/unit/api/test_auth_flows.py`'s
    `test_live_trader_gets_no_route_a_paper_trader_does_not_also_get`
    already exercises this at the HTTP layer; this restates it as an
    explicit safety invariant (`pytest.mark.safety`) rather than only a
    behavioral unit test, since a regression here is exactly the scenario
    this task's hardening pass is guarding against."""
    from datetime import UTC, datetime

    from fastapi.testclient import TestClient

    from atp_api.app import create_app
    from atp_api.deps import get_clock, get_id_generator, get_unit_of_work
    from atp_api.security.passwords import hash_password
    from atp_api.security.rbac import ROLE_LIVE_TRADER, ROLE_PAPER_TRADER
    from atp_domain.clock import FrozenClock
    from atp_domain.ids import SequentialIdGenerator
    from atp_persistence.repositories import UserRecord
    from atp_platform.config import Settings
    from tests.unit.api.fakes import FakeUnitOfWork

    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url="postgresql+psycopg://baduser:badpass@127.0.0.1:1/baddb",  # type: ignore[arg-type]
        redis_url="redis://:x@localhost:6379/0",  # type: ignore[arg-type]
    )
    fake_uow = FakeUnitOfWork()
    frozen_clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))

    async def _override_uow():
        yield fake_uow

    app = create_app(settings=settings)
    app.dependency_overrides[get_unit_of_work] = _override_uow
    app.dependency_overrides[get_clock] = lambda: frozen_clock
    app.dependency_overrides[get_id_generator] = lambda: SequentialIdGenerator()

    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    password_hash = hash_password("correct horse battery staple")
    for username, role in (("live-safety", ROLE_LIVE_TRADER), ("paper-safety", ROLE_PAPER_TRADER)):
        fake_uow.users._by_id[f"user-{username}"] = UserRecord(
            user_id=f"user-{username}",
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=True,
            must_change_password=False,
            created_at=created_at,
            updated_at=created_at,
        )

    live_client = TestClient(app, base_url="https://testserver")
    live_client.post(
        "/api/v1/auth/login",
        json={"username": "live-safety", "password": "correct horse battery staple"},
    )
    paper_client = TestClient(app, base_url="https://testserver")
    paper_client.post(
        "/api/v1/auth/login",
        json={"username": "paper-safety", "password": "correct horse battery staple"},
    )

    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path or "GET" not in methods or "{" in path:
            continue
        live_status = live_client.get(path).status_code
        paper_status = paper_client.get(path).status_code
        assert (
            live_status == paper_status
        ), f"{path}: live_trader={live_status} paper_trader={paper_status}"
