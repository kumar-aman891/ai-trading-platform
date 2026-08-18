"""Safety invariants (Phase 1 Step 9, ADR-011): the paper execution gateway
cannot reach a broker/LLM/live-execution code path, and its public surface
accepts a `proposal_id` only - never a symbol, instrument, quantity, price,
order type, side, product, or any other order field directly.

Extends `tests/safety/test_no_execution_path_in_api.py`'s mechanical-not-
review approach to `atp_exec_paper`: an AST import scan (not a runtime
import, so a forbidden import fails this test even if the forbidden package
happens to be uninstalled in this environment) and a signature inspection
of every function `atp_exec_paper.gateway` exposes.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATP_EXEC_PAPER_SRC = _REPO_ROOT / "execution" / "paper" / "src" / "atp_exec_paper"

_FORBIDDEN_IMPORT_ROOTS = (
    "atp_exec_live",
    "atp_api",
    "kiteconnect",
    "openai",
    "anthropic",
    "httpx",
    "requests",
    "aiohttp",
)

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
        "canonical_payload",
        "expected_risk",
    }
)


def _iter_python_files() -> list[Path]:
    return sorted(_ATP_EXEC_PAPER_SRC.rglob("*.py"))


def test_atp_exec_paper_source_tree_exists() -> None:
    assert _ATP_EXEC_PAPER_SRC.is_dir()
    assert _iter_python_files(), "expected atp_exec_paper to contain Python source files"


def test_no_atp_exec_paper_module_imports_broker_llm_or_api_code() -> None:
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


def test_gateway_public_functions_accept_no_raw_order_field() -> None:
    """`run_once`, `execute_proposal`, `run_poll_cycle`, `run_poll_loop` -
    none of them accept anything order-shaped. `proposal_id` is a bare
    identifier string, reloaded from the database by the gateway itself;
    every other parameter is infrastructure (a `uow`/`session_factory`,
    `id_generator`, `clock`, `correlation_id`, batching/timing knobs) -
    never trading data supplied by the caller."""
    from atp_exec_paper import gateway

    public_functions = [
        gateway.execute_proposal,
        gateway.run_once,
        gateway.run_poll_cycle,
        gateway.run_poll_loop,
    ]
    for func in public_functions:
        signature = inspect.signature(func)
        parameter_names = set(signature.parameters)
        offending = parameter_names & _FORBIDDEN_ORDER_FIELD_PARAMETER_NAMES
        assert (
            offending == set()
        ), f"{func.__qualname__} accepts order-shaped parameter(s) {offending}"


def test_atp_exec_paper_never_imports_the_low_level_minting_primitives() -> None:
    """`atp_domain.risk.engine.mint_intent_for_decision` is the only
    sanctioned way for `atp_exec_paper` to obtain an `ApprovedOrderIntent`
    - it wraps the process-wide `MintingCapability` `atp_domain.risk.engine`
    claims once at import time. `atp_exec_paper` must never import
    `mint_approved_order_intent` or `issue_minting_capability` directly
    (ADR-008): doing so would either fail (the capability was already
    claimed by `atp_domain.risk.engine`) or, if it somehow succeeded,
    would mean two modules held authority to mint an intent instead of
    one."""
    for path in _iter_python_files():
        source = path.read_text(encoding="utf-8")
        assert (
            "mint_approved_order_intent" not in source
        ), f"{path} imports the raw minting function"
        assert "issue_minting_capability" not in source, f"{path} imports the capability issuer"


def test_simulator_never_accepts_a_caller_supplied_price() -> None:
    """`simulate_fill`'s only price source is `proposal.limit_price`,
    reached through the reloaded `TradeProposal` object - there is no
    separate `price`/`limit_price`/`reference_price` parameter a caller
    could substitute a value through."""
    from atp_exec_paper.simulator import simulate_fill

    signature = inspect.signature(simulate_fill)
    parameter_names = set(signature.parameters)
    offending = parameter_names & (_FORBIDDEN_ORDER_FIELD_PARAMETER_NAMES - {"instrument_id"})
    assert offending == set(), f"simulate_fill accepts order-shaped parameter(s) {offending}"
