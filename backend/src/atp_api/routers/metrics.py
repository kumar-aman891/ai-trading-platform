"""`GET /metrics` - the Prometheus scrape endpoint.

Deliberately unversioned and outside `/api/v1`, and deliberately
unauthenticated - mirrors `atp_api.routers.health`'s `/healthz`/`/readyz`
exactly (infrastructure-facing, not part of the versioned application
API surface, no session cookie a scraper could present). Reads
`atp_platform.metrics.PLATFORM_REGISTRY` only; this route defines no
metric of its own.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from atp_platform.metrics import PLATFORM_REGISTRY

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(PLATFORM_REGISTRY), media_type=CONTENT_TYPE_LATEST)
