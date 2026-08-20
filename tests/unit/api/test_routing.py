"""ROUTING: explicit API prefixes, no /live route, no session/execution
route - Phase 1 must not introduce a route that creates or executes an
Order.

`login`/`logout` are no longer forbidden substrings as of Phase 1 Step 8 -
`POST /api/v1/auth/login` and `POST /api/v1/auth/logout` are the two
explicitly-sanctioned authentication routes that step introduces (see
`tests/safety/test_no_execution_path_in_api.py`'s allow-list for the
mechanical proof that nothing else gained a mutating method).

`paper`/`proposal` are no longer forbidden substrings as of Phase 1 Step 10 -
`atp_api.routers.paper`'s routes (`POST /api/v1/paper/proposals`,
`GET /api/v1/paper/proposals[/{id}]`, `/positions`, `/cash`) are the
explicitly-sanctioned, safety-reviewed PAPER intake/ledger routes that step
introduces (ADR-012). `order`/`execute`/`live`/`session` remain banned
unconditionally - a `TradeProposal` is recorded, not an `Order`, and no
path here is ever named around one (D3, this module's own docstring)."""

from __future__ import annotations

from atp_api.app import create_app
from atp_platform.config import Settings

_FORBIDDEN_PATH_SUBSTRINGS = (
    "live",
    "session",
    "execute",
    "order",
)


def _all_route_paths(settings: Settings) -> list[str]:
    app = create_app(settings=settings)
    return [route.path for route in app.routes if hasattr(route, "path")]


def test_no_route_path_contains_a_forbidden_execution_or_auth_term(settings: Settings) -> None:
    for path in _all_route_paths(settings):
        lowered = path.lower()
        for term in _FORBIDDEN_PATH_SUBSTRINGS:
            assert term not in lowered, f"route {path!r} contains forbidden term {term!r}"


def test_versioned_api_routes_are_all_under_api_v1(settings: Settings) -> None:
    versioned = [
        path
        for path in _all_route_paths(settings)
        if path
        not in ("/healthz", "/readyz", "/metrics", "/docs", "/openapi.json", "/openapi.json/")
        and not path.startswith("/docs")
        and not path.startswith("/openapi")
    ]
    for path in versioned:
        assert path.startswith("/api/v1/"), f"unversioned route: {path!r}"


def test_health_routes_exist_and_are_unversioned(settings: Settings) -> None:
    paths = _all_route_paths(settings)
    assert "/healthz" in paths
    assert "/readyz" in paths


def test_metrics_route_exists_and_is_unversioned(settings: Settings) -> None:
    """`/metrics` (Phase 1 Step 13) joins `/healthz`/`/readyz` as an
    infrastructure-facing, unversioned route - not part of `/api/v1`."""
    app = create_app(settings=settings)
    metrics_routes = [
        route for route in app.routes if hasattr(route, "path") and route.path == "/metrics"
    ]
    assert metrics_routes, "expected a GET /metrics route"
    for route in metrics_routes:
        methods = getattr(route, "methods", set())
        assert methods <= {"GET", "HEAD"}


def test_system_status_route_exists(settings: Settings) -> None:
    assert "/api/v1/system/status" in _all_route_paths(settings)


def test_kill_switch_read_route_exists_with_no_mutation_method(settings: Settings) -> None:
    app = create_app(settings=settings)
    kill_switch_routes = [
        route
        for route in app.routes
        if hasattr(route, "path") and route.path == "/api/v1/kill-switches"
    ]
    assert kill_switch_routes, "expected a GET /api/v1/kill-switches route"
    for route in kill_switch_routes:
        methods = getattr(route, "methods", set())
        assert methods <= {"GET", "HEAD"}, f"unexpected mutating method on kill-switches: {methods}"


def test_audit_route_exists_and_is_get_only(settings: Settings) -> None:
    app = create_app(settings=settings)
    audit_routes = [
        route
        for route in app.routes
        if hasattr(route, "path") and route.path == "/api/v1/audit/events"
    ]
    assert audit_routes
    for route in audit_routes:
        methods = getattr(route, "methods", set())
        assert methods <= {"GET", "HEAD"}


_ALLOWED_MUTATING_ROUTES = {
    "/api/v1/auth/login",
    "/api/v1/auth/logout",
    "/api/v1/paper/proposals",
    "/api/v1/kill-switches/{switch_id}/engage",
    "/api/v1/kill-switches/{switch_id}/disengage",
}


def test_only_get_head_and_the_sanctioned_post_routes_exist(settings: Settings) -> None:
    """No PUT/PATCH/DELETE route exists anywhere, and the only POST routes
    are the two Step 8 authentication ones, Step 10's PAPER proposal
    intake, and Step 14's kill-switch engage/disengage - nothing in this
    app can create or execute an Order (intake records a TradeProposal
    only, ADR-012), and the two kill-switch routes accept a `switch_id`
    for only four of the six ADR-007 scopes, never `GLOBAL_LIVE`/
    `LIVE_ACCOUNT` (`test_kill_switches.py`,
    `tests/safety/test_kill_switch_mutation_boundary.py`)."""
    app = create_app(settings=settings)
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods is None:
            continue
        path = getattr(route, "path", "")
        allowed = {"GET", "HEAD", "OPTIONS"} | (
            {"POST"} if path in _ALLOWED_MUTATING_ROUTES else set()
        )
        assert methods <= allowed, f"route {path!r} exposes mutating method(s): {methods}"
