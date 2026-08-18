"""OpenAPI metadata: title, version, description, tag grouping.

Deliberately contains no route registration - importing this module has no
side effect on the app, so `API_VERSION` can be reused by
`atp_api.routers.system` without circular-importing `atp_api.app`.
"""

from __future__ import annotations

API_TITLE = "AI Trading Platform API"
API_VERSION = "0.1.0"
API_DESCRIPTION = (
    "Phase 1 Step 10 application/service foundation. PAPER mode only - no "
    "live execution path exists in this build (ADR-005, ADR-008). "
    "Authentication, session management, RBAC, and CSRF are implemented "
    "(Step 8); PAPER trade-proposal submission is implemented but performs "
    "no risk evaluation of its own (Step 10, ADR-012) - every proposal is "
    "evaluated by the separate, deterministic `atp_exec_paper` gateway."
)

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "health", "description": "Process liveness and dependency readiness probes."},
    {"name": "system", "description": "Safe, read-only system metadata. No secrets or topology."},
    {
        "name": "kill-switches",
        "description": "Read-only kill-switch state. No mutation route exists yet.",
    },
    {
        "name": "audit",
        "description": "Read-only, paginated audit event history (append-only, ADR-010).",
    },
    {"name": "auth", "description": "Login, logout, and current-principal session endpoints."},
    {
        "name": "instruments",
        "description": "Read-only instrument reference data (Phase 1 FIXTURE seed set).",
    },
    {
        "name": "paper",
        "description": (
            "PAPER trade-proposal submission and ledger reads (proposals, positions, "
            "cash). Intake performs no risk evaluation - ADR-012."
        ),
    },
]
