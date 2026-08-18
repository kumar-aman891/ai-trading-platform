"""Safety invariants (Phase 1 Step 10, ADR-012): `atp_api`'s PAPER
trade-proposal intake performs structural validation only - it can never
evaluate risk, mint an `ApprovedOrderIntent`, or accept a caller-supplied
value for a server-set field.

Extends `tests/safety/test_no_execution_path_in_api.py`'s mechanical-not-
review approach: an AST import scan (not a runtime import, so a forbidden
import fails this test even if the module happens to be importable in this
environment) for the operations the `atp_api ↛ atp_exec_paper` import-
linter contract does not, by itself, rule out - `evaluate`/
`mint_intent_for_decision` live in `atp_domain.risk.engine`, not
`atp_exec_paper`. `atp_domain.risk.engine.RiskDecision` - a plain,
frozen dataclass, not an operation - is deliberately **not** forbidden:
`atp_api.services.paper_ledger` legitimately imports it to type the
already-computed decisions it reads back for the ledger view
(`GET /api/v1/paper/proposals/{proposal_id}`); reading a decision someone
else computed is not evaluating one.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATP_API_SRC = _REPO_ROOT / "backend" / "src" / "atp_api"

# Specific callables, not the modules that also declare harmless read-only
# types (`RiskDecision`) - see the module docstring above for why a
# module-level ban would be too coarse.
_FORBIDDEN_QUALIFIED_NAMES = (
    "atp_domain.risk.engine.evaluate",
    "atp_domain.risk.engine.mint_intent_for_decision",
)
# `atp_domain.intents` (ApprovedOrderIntent construction) has no legitimate
# read-only use in atp_api at all - the ledger reads an intent's *effects*
# (the resulting Order/Fill), never the intent object itself
# (docs/schemas/order_intent.md: atp_api has no SELECT-worthy use for it
# either - the narrowest write path in the schema).
_FORBIDDEN_MODULE_PREFIXES = ("atp_domain.intents",)


def _iter_python_files() -> list[Path]:
    return sorted(_ATP_API_SRC.rglob("*.py"))


def _module_is_forbidden(dotted_module: str) -> bool:
    return any(
        dotted_module == prefix or dotted_module.startswith(prefix + ".")
        for prefix in _FORBIDDEN_MODULE_PREFIXES
    )


def test_atp_api_never_imports_the_risk_evaluation_or_intent_minting_operations() -> None:
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not _module_is_forbidden(
                        alias.name
                    ), f"{path.relative_to(_REPO_ROOT)} imports forbidden module {alias.name!r}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not _module_is_forbidden(
                    node.module
                ), f"{path.relative_to(_REPO_ROOT)} imports forbidden module {node.module!r}"
                for alias in node.names:
                    qualified = f"{node.module}.{alias.name}"
                    assert (
                        qualified not in _FORBIDDEN_QUALIFIED_NAMES
                    ), f"{path.relative_to(_REPO_ROOT)} imports forbidden name {qualified!r}"


def test_submit_proposal_request_has_no_server_set_field() -> None:
    """`mode`, `created_by`, `proposal_id`, and `created_at` are always
    server-set (ADR-012 point 4) - a client cannot request or override any
    of them via the request body. `ApiModel`'s `extra="forbid"` also
    rejects an unrecognized field outright, but this asserts the stronger
    claim: the field does not exist on the schema at all, so there is no
    code path that could ever read a caller-supplied value for it."""
    from atp_api.schemas.paper import SubmitProposalRequest

    forbidden_fields = {"mode", "created_by", "proposal_id", "created_at"}
    assert forbidden_fields.isdisjoint(SubmitProposalRequest.model_fields)


def test_submit_proposal_request_rejects_a_client_supplied_server_set_field() -> None:
    from pydantic import ValidationError

    from atp_api.schemas.paper import SubmitProposalRequest

    base = {
        "instrument_id": "some-instrument",
        "side": "BUY",
        "quantity": "10",
        "order_type": "LIMIT",
        "limit_price": "100",
        "product": "CNC",
        "client_request_id": "req-1",
    }
    for forbidden_field, value in (
        ("mode", "PAPER"),
        ("created_by", "someone"),
        ("proposal_id", "00000000-0000-7000-8000-000000000001"),
        ("created_at", "2026-01-01T00:00:00Z"),
    ):
        with pytest.raises(ValidationError):
            SubmitProposalRequest.model_validate(base | {forbidden_field: value})


def test_submit_proposal_response_never_carries_a_risk_outcome() -> None:
    """A 2xx from intake means recorded, not approved (ADR-012 point 1) -
    the response schema has no field a risk outcome could ever be written
    into. Only `GET /api/v1/paper/proposals/{proposal_id}` (a distinct
    schema, `ProposalResponse`) is permitted to carry a `decision`."""
    from atp_api.schemas.paper import SubmitProposalResponse

    forbidden_fields = {"decision", "outcome", "rule_results", "approved", "risk_decision"}
    assert forbidden_fields.isdisjoint(SubmitProposalResponse.model_fields)


_FORBIDDEN_CALL_NAMES = frozenset(
    {"evaluate", "mint_intent_for_decision", "mint_approved_order_intent"}
)


def test_no_atp_api_module_calls_the_risk_engine_or_mints_an_intent() -> None:
    """Defense in depth beyond the import scan: even a re-exported alias or
    a fully-qualified attribute call (`risk_engine.evaluate(...)`) would
    still show up as a `Call` node naming one of these functions. An AST
    check, not a substring search, so this test cannot be tripped by a
    docstring/comment that merely *names* the forbidden functions (as this
    very module's own docstrings do, to explain what they forbid)."""
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            assert (
                name not in _FORBIDDEN_CALL_NAMES
            ), f"{path.relative_to(_REPO_ROOT)} calls forbidden function {name!r}"
