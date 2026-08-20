"""Safety invariant #19 (ADR-014 §G, ADR-015): `atp_strategy` cannot reach
an order execution path.

Mirrors `tests/safety/test_no_execution_path_in_worker.py`'s mechanical-
not-review approach exactly: an AST import scan (fails on a forbidden
import even if the forbidden package happens to be uninstalled here) plus
a signature inspection of every public function `atp_strategy` defines.
Milestone 2B ships no runner, registry, or strategy evaluation - this test
proves the *boundary* the future runner (Milestone 2C) must stay inside,
before any code that could violate it exists.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATP_STRATEGY_SRC = _REPO_ROOT / "strategy" / "src" / "atp_strategy"

#: Whole packages `atp_strategy` must never import any part of (ADR-014
#: §G): `atp_strategy` holds no grant on any execution-outcome table and
#: no HTTP route, so it has no legitimate reason to reach either the paper
#: execution gateway or the API layer.
_FORBIDDEN_IMPORT_ROOTS = (
    "atp_exec_paper",
    "atp_api",
)

#: Specific submodules, not whole packages, that `atp_strategy` must never
#: import (ADR-014 §G): `atp_domain` and `atp_persistence` themselves are
#: fine (the kill-switch adapter imports `atp_domain.killswitch`/
#: `atp_domain.errors`, and `atp_strategy.uow` imports
#: `atp_persistence.repositories.*`) - only the order-intent-minting and
#: risk-evaluation primitives, and the `paper`-mode ORM models (order/
#: fill/intent/position/cash-ledger rows `atp_strategy` holds no grant on),
#: are off limits.
_FORBIDDEN_IMPORT_PREFIXES = (
    "atp_domain.intents",
    "atp_domain.risk.engine",
    "atp_persistence.models.paper",
)

#: No broker/Kite/MCP or LLM module exists anywhere in this repository yet
#: (standing Phase 1 invariant) - named here so this test fails loudly, by
#: import-root match, the moment one is added and `atp_strategy` reaches
#: for it, rather than relying on that invariant being caught elsewhere
#: first.
_FORBIDDEN_IMPORT_ROOTS += ("kite", "atp_broker", "atp_llm")

_FORBIDDEN_ORDER_FIELD_PARAMETER_NAMES = frozenset(
    {
        "symbol",
        "instrument",
        "instrument_id",
        "quantity",
        "price",
        "limit_price",
        "trigger_price",
        "order_type",
        "side",
        "product",
    }
)


def _iter_python_files() -> list[Path]:
    return sorted(_ATP_STRATEGY_SRC.rglob("*.py"))


def _imported_fullnames(tree: ast.Module) -> list[str]:
    """Every dotted module path this file imports, as a single string per
    import - `import a.b.c` and `from a.b import c` both yield
    `"a.b.c"`, so a forbidden *submodule* (not just a forbidden root
    package) can be detected by prefix match against the result."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_atp_strategy_source_tree_exists() -> None:
    assert _ATP_STRATEGY_SRC.is_dir()
    assert _iter_python_files(), "expected atp_strategy to contain Python source files"


def test_no_atp_strategy_module_imports_the_execution_gateway_api_layer_or_broker_llm() -> None:
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fullname in _imported_fullnames(tree):
            root = fullname.split(".")[0]
            assert (
                root not in _FORBIDDEN_IMPORT_ROOTS
            ), f"{path.relative_to(_REPO_ROOT)} imports forbidden module {fullname!r}"


def test_no_atp_strategy_module_imports_intent_minting_or_risk_engine_or_paper_models() -> None:
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fullname in _imported_fullnames(tree):
            for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                offends = fullname == forbidden or fullname.startswith(forbidden + ".")
                assert not offends, (
                    f"{path.relative_to(_REPO_ROOT)} imports forbidden module {fullname!r} "
                    f"(matches forbidden prefix {forbidden!r})"
                )


def test_no_atp_strategy_module_imports_httpx_requests_or_aiohttp() -> None:
    """No market-data/broker network egress (ADR-006) - redundant with the
    import-linter no-egress contract, proven again here by direct AST scan
    so this file stays a self-contained mechanical proof of the whole
    boundary, not just the parts import-linter alone covers."""
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fullname in _imported_fullnames(tree):
            root = fullname.split(".")[0]
            assert root not in {
                "httpx",
                "requests",
                "aiohttp",
            }, f"{path.relative_to(_REPO_ROOT)} imports forbidden egress module {fullname!r}"


def _iter_strategy_modules() -> list[object]:
    """Every module in the `atp_strategy` package, imported for real (not
    parsed) so `inspect.signature` can be used - `pkgutil.walk_packages`
    rather than a hand-maintained list, so a new module added later is
    covered automatically instead of silently escaping this test."""
    import atp_strategy

    modules: list[object] = [atp_strategy]
    for module_info in pkgutil.walk_packages(atp_strategy.__path__, prefix="atp_strategy."):
        modules.append(importlib.import_module(module_info.name))
    return modules


def _iter_public_functions_defined_in_atp_strategy() -> list[object]:
    """Every public (non-underscore-prefixed) top-level function actually
    *defined* in an `atp_strategy` module - `func.__module__ ==
    module.__name__` excludes names merely imported into a module's
    namespace, which are already checked at their own definition site and
    would otherwise be inspected twice."""
    functions: list[object] = []
    for module in _iter_strategy_modules():
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_"):
                continue
            if func.__module__ != module.__name__:
                continue
            functions.append(func)
    return functions


def test_no_public_atp_strategy_function_accepts_a_raw_order_field() -> None:
    """ADR-014 §G. Every public function in `atp_strategy` deals only in
    infrastructure (a session factory, a `uow`, snapshots/mappings) -
    never a bare symbol, quantity, price, side, or order-type parameter a
    caller could substitute a value through."""
    functions = _iter_public_functions_defined_in_atp_strategy()
    assert functions, "expected at least one public function in atp_strategy"
    for func in functions:
        signature = inspect.signature(func)
        parameter_names = set(signature.parameters)
        offending = parameter_names & _FORBIDDEN_ORDER_FIELD_PARAMETER_NAMES
        assert (
            offending == set()
        ), f"{func.__qualname__} accepts order-shaped parameter(s) {offending}"


def test_strategy_unit_of_work_exposes_no_forbidden_repository() -> None:
    """ADR-015: `StrategyUnitOfWork` must expose exactly the four
    repositories `atp_strategy`'s grants permit (instruments, kill
    switches, trade proposals, audit writer) - never users, sessions,
    job_queue, orders, fills, positions, cash_ledger, order_intents, risk
    decisions, or risk_config, none of which this role holds any
    privilege on."""
    from atp_strategy.uow import StrategyUnitOfWork

    allowed_attributes = {"instruments", "kill_switches", "trade_proposals", "audit"}
    forbidden_attributes = {
        "users",
        "sessions",
        "jobs",
        "job_queue",
        "orders",
        "fills",
        "positions",
        "cash_ledger",
        "order_intents",
        "risk_decisions",
        "risk_config",
    }

    init_source = inspect.getsource(StrategyUnitOfWork.__init__)
    for forbidden in forbidden_attributes:
        assert (
            f"self.{forbidden}" not in init_source
        ), f"StrategyUnitOfWork must not expose self.{forbidden}"
    for allowed in allowed_attributes:
        assert f"self.{allowed}" in init_source, f"StrategyUnitOfWork must expose self.{allowed}"
