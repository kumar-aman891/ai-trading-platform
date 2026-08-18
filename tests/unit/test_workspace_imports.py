"""Smoke test: every Phase 1 package is installed and importable in the shared
uv workspace environment.

This is infrastructure verification, not business logic — it exists to prove
the workspace scaffold from Phase 1 Step 2 actually wires together, so later
steps have a working `uv sync` + `pytest` baseline to build on.

LIVE-nonexistence assertions live in tests/safety/ (they are safety
invariants from the approved Phase 1 plan §14, not workspace smoke tests).
"""

from __future__ import annotations

import importlib

import pytest

PHASE_1_PACKAGES = [
    "atp_domain",
    "atp_domain.risk",
    "atp_domain.risk.catalog",
    "atp_domain.ports",
    "atp_platform",
    "atp_persistence",
    "atp_persistence.models",
    "atp_persistence.repositories",
    "atp_api",
    "atp_api.security",
    "atp_api.routers",
    "atp_api.routers.instruments",
    "atp_api.routers.paper",
    "atp_api.schemas",
    "atp_api.schemas.instrument",
    "atp_api.schemas.paper",
    "atp_api.middleware",
    "atp_api.services",
    "atp_api.services.instruments",
    "atp_api.services.paper_ledger",
    "atp_api.services.paper_proposals",
    "atp_api.bootstrap",
    "atp_exec_paper",
    "atp_exec_paper.gateway",
    "atp_exec_paper.kill_switch_adapter",
    "atp_exec_paper.risk_runner",
    "atp_exec_paper.simulator",
    "atp_exec_paper.uow",
    "atp_worker",
]


@pytest.mark.parametrize("module_name", PHASE_1_PACKAGES)
def test_phase_1_package_is_importable(module_name: str) -> None:
    importlib.import_module(module_name)
