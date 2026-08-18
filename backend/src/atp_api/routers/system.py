"""`GET /api/v1/system/status`.

No request input (query, header, or body) is ever read into this route's
response - `mode` comes only from server-side `Settings.trading_mode`. See
atp_api.schemas.system's module docstring for the Pydantic-level backstop.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from atp_api.deps import get_session_factory, get_settings, require_permission
from atp_api.openapi import API_VERSION
from atp_api.schemas.system import DependencyHealth, SystemStatusResponse
from atp_api.security.rbac import Permission
from atp_api.services.system_status import build_system_status
from atp_platform.config import Settings

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get(
    "/status",
    response_model=SystemStatusResponse,
    dependencies=[Depends(require_permission(Permission.READ_SYSTEM))],
)
async def get_system_status(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> SystemStatusResponse:
    session_factory = get_session_factory(request)
    view = await build_system_status(
        settings=settings, session_factory=session_factory, app_version=API_VERSION
    )
    return SystemStatusResponse(
        mode=view.mode,  # type: ignore[arg-type]  # Settings guarantees "PAPER"; see schema docstring
        version=view.version,
        environment=view.environment,
        migration_version=view.migration_version,
        degraded=view.degraded,
        dependencies=[
            DependencyHealth(name=dep.name, status="OK" if dep.ok else "FAIL")
            for dep in view.dependencies
        ],
    )
