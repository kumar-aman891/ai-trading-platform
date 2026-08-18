"""OpenAPI metadata: title, version, description, tag grouping.

Deliberately contains no route registration - importing this module has no
side effect on the app, so `API_VERSION` can be reused by
`atp_api.routers.system` without circular-importing `atp_api.app`.
"""

from __future__ import annotations

API_TITLE = "AI Trading Platform API"
API_VERSION = "0.1.0"
API_DESCRIPTION = (
    "Phase 1 Step 7 application/service foundation. PAPER mode only - no "
    "live execution path exists in this build (ADR-005, ADR-008). "
    "Authentication and authorization are not implemented yet (Step 8); "
    "every route documented here is read-only."
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
]
