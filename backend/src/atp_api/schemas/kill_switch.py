"""`GET /api/v1/kill-switches` response shape - read-only (Phase 1 Step 7).

`updated_by` (a `core.users.user_id`) is deliberately omitted: there is no
authenticated caller identity system yet (Step 8), so nothing justifies
exposing an internal user reference to an unauthenticated caller.
Mutation endpoints do not exist in this router at all - see
atp_api.routers.kill_switches's module docstring.
"""

from __future__ import annotations

from datetime import datetime

from atp_api.schemas.common import ApiModel


class KillSwitchStateResponse(ApiModel):
    switch_id: str
    engaged: bool
    updated_at: datetime
    reason: str | None


class KillSwitchListResponse(ApiModel):
    items: list[KillSwitchStateResponse]
