"""`GET /api/v1/kill-switches` response shape (Phase 1 Step 7) and the
`POST .../engage`/`.../disengage` request shape (Phase 1 Step 14,
ADR-007).

`updated_by` (a `core.users.user_id`) is deliberately omitted from
`KillSwitchStateResponse`: it was originally omitted because no
authenticated caller identity system existed yet (Step 7); it stays
omitted now that one does because nothing about this read response
justifies exposing one internal user reference to any caller with
`READ_KILL_SWITCH` - narrower than `ENGAGE_KILL_SWITCH`/
`DISENGAGE_KILL_SWITCH` - alongside every other viewer of this endpoint.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from atp_api.schemas.common import ApiModel


class KillSwitchStateResponse(ApiModel):
    switch_id: str
    engaged: bool
    updated_at: datetime
    reason: str | None


class KillSwitchListResponse(ApiModel):
    items: list[KillSwitchStateResponse]


class KillSwitchMutationRequest(ApiModel):
    """Body for both `POST .../engage` and `.../disengage` - the verb
    itself comes from the URL, matching `atp_api.routers.paper`'s existing
    convention of not repeating an action already named by the route.
    `reason` is unconditionally required at this layer, not merely when
    `core.kill_switch_state`'s `reason_required` CHECK would otherwise
    fire (that constraint only fires when `updated_by` is set, which is
    always true for a route only an authenticated principal can reach) -
    `core.kill_switch_history.reason` is `NOT NULL` unconditionally
    (`docs/schemas/kill_switch_history.md`), so a request this route
    accepts must always be able to satisfy it, including on the rare
    already-in-that-state no-op path where no row is ever written and the
    column-level constraint never actually runs."""

    reason: str = Field(min_length=1, max_length=2000)
