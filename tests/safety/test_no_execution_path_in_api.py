"""Safety invariant (Phase 1 Step 7): the FastAPI application cannot create
or execute an Order.

Extends tests/safety/test_no_live_execution.py's mechanical-not-review
approach to the new application layer: `atp_api` must not import
`atp_exec_paper`/broker/LLM code, and every route the built app actually
serves must be a safe (GET/HEAD) method, with the sole allow-listed
exception of the Phase 1 Step 8 login/logout POST routes (pure
authentication plumbing - see `_ALLOWED_MUTATING_ROUTES` below) - so "the
API must not be capable of creating or executing an Order" is true
structurally, not by convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATP_API_SRC = _REPO_ROOT / "backend" / "src" / "atp_api"

_FORBIDDEN_IMPORT_ROOTS = (
    "atp_exec_paper",
    "atp_exec_live",
    "kiteconnect",
    "openai",
    "anthropic",
)


def _iter_python_files() -> list[Path]:
    return sorted(_ATP_API_SRC.rglob("*.py"))


def test_atp_api_source_tree_exists() -> None:
    assert _ATP_API_SRC.is_dir()
    assert _iter_python_files(), "expected atp_api to contain Python source files"


def test_no_atp_api_module_imports_execution_broker_or_llm_code() -> None:
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                assert (
                    root not in _FORBIDDEN_IMPORT_ROOTS
                ), f"{path.relative_to(_REPO_ROOT)} imports forbidden module {name!r}"


def test_no_execution_live_directory_exists() -> None:
    assert not (_REPO_ROOT / "execution" / "live").exists()


# Phase 1 Step 8 introduces exactly two POST routes - login and logout -
# both authentication plumbing, neither capable of creating/mutating an
# Order, a RiskConfig, or any kill-switch state. Phase 1 Step 10 adds a
# third: POST /api/v1/paper/proposals records a TradeProposal only - it
# never evaluates risk, mints an ApprovedOrderIntent, or writes an Order/
# Fill/Position (ADR-012, tests/safety/test_proposal_intake_is_not_a_risk_gate.py).
# Every other route in the app (health, system, kill-switches, audit, /me,
# instruments, the paper ledger reads) stays GET-only.
_ALLOWED_MUTATING_ROUTES: dict[str, frozenset[str]] = {
    "/api/v1/auth/login": frozenset({"POST"}),
    "/api/v1/auth/logout": frozenset({"POST"}),
    "/api/v1/paper/proposals": frozenset({"POST"}),
}


def test_built_app_exposes_no_unexpected_mutating_route() -> None:
    """Every route in the fully-wired app is GET/HEAD/OPTIONS only, except
    the explicitly allow-listed Step 8 auth routes above - proven against
    the actual built `FastAPI` instance, not by reading source."""
    from atp_api.app import create_app
    from atp_platform.config import Settings

    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url="postgresql+psycopg://baduser:badpass@127.0.0.1:1/baddb",  # type: ignore[arg-type]
        redis_url="redis://:x@localhost:6379/0",  # type: ignore[arg-type]
    )
    app = create_app(settings=settings)

    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods is None:
            continue
        path = getattr(route, "path", "")
        allowed = {"GET", "HEAD", "OPTIONS"} | _ALLOWED_MUTATING_ROUTES.get(path, frozenset())
        assert methods <= allowed, f"route {path!r} exposes {methods}"
        # Regardless of the allow-list, PUT/PATCH/DELETE are never
        # permitted anywhere - login/logout are POST, nothing is ever a
        # resource update or delete.
        assert not methods & {"PUT", "PATCH", "DELETE"}, f"route {path!r} exposes {methods}"


def test_built_app_has_no_route_path_named_order_or_execute() -> None:
    from atp_api.app import create_app
    from atp_platform.config import Settings

    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url="postgresql+psycopg://baduser:badpass@127.0.0.1:1/baddb",  # type: ignore[arg-type]
        redis_url="redis://:x@localhost:6379/0",  # type: ignore[arg-type]
    )
    app = create_app(settings=settings)

    for route in app.routes:
        path = getattr(route, "path", "")
        lowered = path.lower()
        assert "order" not in lowered
        assert "execute" not in lowered
        assert "/live" not in lowered
