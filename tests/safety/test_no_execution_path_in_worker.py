"""Safety invariant #17 (ADR-013 §13): `atp_worker` cannot reach an order
execution path, and its `HANDLER_REGISTRY` cannot silently drift from
`core.job_queue`'s own `valid_job_type` CHECK constraint in either
direction.

Extends `tests/safety/test_no_execution_path_in_atp_exec_paper.py`'s
mechanical-not-review approach: an AST import scan (fails on a forbidden
import even if the forbidden package happens to be uninstalled here) plus
a signature inspection of every public function `atp_worker` defines.
Point 3 (registry <-> CHECK-constraint parity) has no equivalent in the
`atp_exec_paper` suite - it is specific to ADR-013 §11's two-independent
-declarations-plus-parity design, deliberately not derived from a single
source (see `atp_worker.registry`'s own docstring for why deriving one
from the other would make this exact test tautological).
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.safety

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ATP_WORKER_SRC = _REPO_ROOT / "workers" / "src" / "atp_worker"

#: Whole packages `atp_worker` must never import any part of (ADR-013 §1,
#: §13 point 1): `atp_worker` holds zero grants on any `paper`/`live`
#: table and no HTTP route, so it has no legitimate reason to reach
#: either the paper execution gateway or the API layer.
_FORBIDDEN_IMPORT_ROOTS = (
    "atp_exec_paper",
    "atp_api",
)

#: Specific submodules, not whole packages, that `atp_worker` must never
#: import (ADR-013 §13 point 1): `atp_domain` and `atp_persistence`
#: themselves are fine (every handler imports `atp_domain.clock`/`ids`,
#: and `atp_worker.uow` imports `atp_persistence.repositories.*`) - only
#: the order-intent-minting and risk-evaluation primitives, and the
#: `paper`-mode ORM models, are off limits.
_FORBIDDEN_IMPORT_PREFIXES = (
    "atp_domain.intents",
    "atp_domain.risk.engine",
    "atp_persistence.models.paper",
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
    }
)


def _iter_python_files() -> list[Path]:
    return sorted(_ATP_WORKER_SRC.rglob("*.py"))


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


def test_atp_worker_source_tree_exists() -> None:
    assert _ATP_WORKER_SRC.is_dir()
    assert _iter_python_files(), "expected atp_worker to contain Python source files"


def test_no_atp_worker_module_imports_the_execution_gateway_or_the_api_layer() -> None:
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fullname in _imported_fullnames(tree):
            root = fullname.split(".")[0]
            assert (
                root not in _FORBIDDEN_IMPORT_ROOTS
            ), f"{path.relative_to(_REPO_ROOT)} imports forbidden module {fullname!r}"


def test_no_atp_worker_module_imports_intent_minting_or_risk_engine_or_paper_models() -> None:
    for path in _iter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fullname in _imported_fullnames(tree):
            for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                offends = fullname == forbidden or fullname.startswith(forbidden + ".")
                assert not offends, (
                    f"{path.relative_to(_REPO_ROOT)} imports forbidden module {fullname!r} "
                    f"(matches forbidden prefix {forbidden!r})"
                )


def _iter_worker_modules() -> list[object]:
    """Every module in the `atp_worker` package, imported for real (not
    parsed) so `inspect.signature` can be used - `pkgutil.walk_packages`
    rather than a hand-maintained list, so a new module added later is
    covered automatically instead of silently escaping this test."""
    import atp_worker

    modules: list[object] = [atp_worker]
    for module_info in pkgutil.walk_packages(atp_worker.__path__, prefix="atp_worker."):
        modules.append(importlib.import_module(module_info.name))
    return modules


def _iter_public_functions_defined_in_atp_worker() -> list[object]:
    """Every public (non-underscore-prefixed) top-level function actually
    *defined* in an `atp_worker` module - `func.__module__ ==
    module.__name__` excludes names merely imported into a module's
    namespace (e.g. `HANDLER_REGISTRY`'s handler imports into
    `registry.py`), which are already checked at their own definition
    site and would otherwise be inspected twice."""
    functions: list[object] = []
    for module in _iter_worker_modules():
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("_"):
                continue
            if func.__module__ != module.__name__:
                continue
            functions.append(func)
    return functions


def test_no_public_atp_worker_function_accepts_a_raw_order_field() -> None:
    """ADR-013 §13 point 2. Every public function in `atp_worker` deals
    only in infrastructure (a `uow`/`uow_factory`, `clock`, `id_generator`,
    `job`/`ClaimedJob`, batching/timing knobs) or, inside a handler, a
    `ClaimedJob` whose `payload` is an opaque `dict[str, object]` - never
    a bare symbol, quantity, price, side, or order-type parameter a
    caller could substitute a value through."""
    functions = _iter_public_functions_defined_in_atp_worker()
    assert functions, "expected at least one public function in atp_worker"
    for func in functions:
        signature = inspect.signature(func)
        parameter_names = set(signature.parameters)
        offending = parameter_names & _FORBIDDEN_ORDER_FIELD_PARAMETER_NAMES
        assert (
            offending == set()
        ), f"{func.__qualname__} accepts order-shaped parameter(s) {offending}"


def _db_job_type_allowlist() -> frozenset[str]:
    """Parses the allowlist out of `JobQueueRow.__table_args__`'s
    `valid_job_type` CHECK constraint's own SQL text - ADR-013 §11
    requires this be parsed, not restated as a second literal, so the
    database and the test cannot silently drift apart the same way the
    database and `HANDLER_REGISTRY` are being checked for here."""
    from atp_persistence.models.core import JobQueueRow

    for constraint in JobQueueRow.__table_args__:
        name = getattr(constraint, "name", None)
        if name and "valid_job_type" in name:
            match = re.search(r"IN\s*\(([^)]*)\)", str(constraint.sqltext))
            assert (
                match is not None
            ), f"could not parse CHECK constraint text: {constraint.sqltext!r}"
            return frozenset(item.strip().strip("'") for item in match.group(1).split(","))
    raise AssertionError("valid_job_type CHECK constraint not found on JobQueueRow.__table_args__")


def test_handler_registry_matches_the_database_job_type_allowlist_bidirectionally() -> None:
    """ADR-013 §11/§13 point 3. `set(HANDLER_REGISTRY)` must equal the
    database's allowlist exactly - not a subset in either direction. A
    registry key with no matching CHECK entry could never be claimed (a
    silently dead handler); a CHECK entry with no registry key would hit
    `NoHandlerRegisteredError` in production the moment such a row was
    ever inserted."""
    from atp_worker.registry import HANDLER_REGISTRY

    db_allowlist = _db_job_type_allowlist()
    registry_keys = frozenset(HANDLER_REGISTRY)

    assert registry_keys == db_allowlist, (
        f"HANDLER_REGISTRY {sorted(registry_keys)} != database allowlist "
        f"{sorted(db_allowlist)} - missing from registry: "
        f"{sorted(db_allowlist - registry_keys)}, missing from CHECK: "
        f"{sorted(registry_keys - db_allowlist)}"
    )


def test_handler_registry_has_exactly_the_three_phase_1_job_types() -> None:
    """A concrete, hardcoded expectation alongside the parity test above
    (which is deliberately relative, not absolute) - if a future change
    ever makes both the registry and the CHECK constraint agree on the
    wrong thing at the same time, this still catches it."""
    from atp_worker.registry import (
        HANDLER_REGISTRY,
        JOB_TYPE_AUDIT_INTEGRITY_CHECK,
        JOB_TYPE_RETENTION,
        JOB_TYPE_SESSION_REAP,
    )

    assert set(HANDLER_REGISTRY) == {
        JOB_TYPE_SESSION_REAP,
        JOB_TYPE_AUDIT_INTEGRITY_CHECK,
        JOB_TYPE_RETENTION,
    }
