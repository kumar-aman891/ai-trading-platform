"""`GET /healthz` (liveness) and `GET /readyz` (readiness).

Deliberately unversioned and outside `/api/v1` - these are
infrastructure/orchestrator-facing probes (load balancer, container
runtime), not part of the versioned application API surface.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from atp_api.deps import get_session_factory
from atp_api.schemas.health import LivenessResponse, ReadinessResponse
from atp_api.services.dependencies import async_readiness, database_check
from atp_platform.health import liveness

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    """Process liveness only. No dependency is consulted, so this can
    never itself fail closed on a database/Redis outage - that is exactly
    what makes it a distinct signal from `/readyz`."""
    assert liveness().ok  # atp_platform.health.liveness() never reports otherwise
    return LivenessResponse(status="OK")


@router.get("/readyz", response_model=ReadinessResponse)
async def readyz(request: Request) -> ReadinessResponse | JSONResponse:
    session_factory = get_session_factory(request)
    aggregate, _ = await async_readiness([lambda: database_check(session_factory)])
    if aggregate.ok:
        return ReadinessResponse(status="OK")
    # 503, not 200 - an orchestrator's readiness probe must see a non-2xx
    # status to stop routing traffic here. `reason` is a fixed, opaque
    # constant - aggregate.detail (dependency names / exception class
    # names) stays server-side only (logged by async_readiness's callers
    # if they choose to; not here).
    return JSONResponse(
        status_code=503,
        content=ReadinessResponse(status="FAIL", reason="dependency_unavailable").model_dump(
            mode="json"
        ),
    )
