"""`GET /api/v1/kill-switches` - read-only.

No mutation route exists in this module, on purpose. Authentication and
authorization do not exist yet (Step 8) - an unauthenticated caller must
never be able to engage or disengage a switch, so the only safe Step 7
scope is a read of current state. This is not an oversight to "fix"
without first building auth/RBAC; see the Step 7 task's "Kill Switch API
Foundation" scope note.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from atp_api.deps import get_kill_switch_repository, require_permission
from atp_api.schemas.kill_switch import KillSwitchListResponse, KillSwitchStateResponse
from atp_api.security.rbac import Permission
from atp_api.services.kill_switches import list_kill_switches
from atp_persistence.repositories import SqlAlchemyKillSwitchStateRepository

router = APIRouter(prefix="/api/v1/kill-switches", tags=["kill-switches"])


@router.get(
    "",
    response_model=KillSwitchListResponse,
    dependencies=[Depends(require_permission(Permission.READ_KILL_SWITCH))],
)
async def get_kill_switches(
    repository: Annotated[SqlAlchemyKillSwitchStateRepository, Depends(get_kill_switch_repository)],
) -> KillSwitchListResponse:
    snapshots = await list_kill_switches(repository)
    return KillSwitchListResponse(
        items=[
            KillSwitchStateResponse(
                switch_id=snapshot.switch_id,
                engaged=snapshot.engaged,
                updated_at=snapshot.updated_at,
                reason=snapshot.reason,
            )
            for snapshot in snapshots
        ]
    )
