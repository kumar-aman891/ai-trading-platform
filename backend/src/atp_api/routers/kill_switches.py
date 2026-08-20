"""`GET /api/v1/kill-switches` (Phase 1 Step 7) and
`POST /api/v1/kill-switches/{switch_id}/engage`/`.../disengage`
(Phase 1 Step 14, ADR-007).

Step 7 shipped read-only on purpose: authentication/RBAC did not exist
yet, so an unauthenticated caller must never engage/disengage a switch,
and the only safe scope was a read of current state (see this module's
prior docstring, and the "Kill Switch API Foundation" scope note).
Auth/RBAC (Step 8) has been complete since then - the two mutation routes
below are that deferred capability, not a reopening of the Step 7
decision: every request still authenticates normally
(`get_current_principal`), still enforces CSRF (`enforce_csrf`, the same
dependency every other state-changing route beyond login/logout uses),
and still enforces a specific `Permission` per route
(`ENGAGE_KILL_SWITCH`/`DISENGAGE_KILL_SWITCH` - ADR-007's asymmetry).

`GLOBAL_LIVE`/`LIVE_ACCOUNT` have no mutation route in Phase 1 and never
will while ADR-007 stands ("No - no route exists to clear it") - both
routes below accept a `switch_id` for *any* of the six scopes and let
`atp_api.services.kill_switches._parse_mutable_switch_id` reject the two
un-clearable ones with `ForbiddenError` (403). This is enforced
mechanically, not by convention alone: see
`tests/safety/test_kill_switch_mutation_boundary.py`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from atp_api.deps import (
    AuthenticatedPrincipal,
    enforce_csrf,
    get_clock,
    get_current_principal,
    get_id_generator,
    get_kill_switch_repository,
    get_unit_of_work,
    require_permission,
)
from atp_api.schemas.kill_switch import (
    KillSwitchListResponse,
    KillSwitchMutationRequest,
    KillSwitchStateResponse,
)
from atp_api.security.rbac import Permission
from atp_api.services import kill_switches as kill_switches_service
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_persistence.db import UnitOfWork
from atp_persistence.repositories import SqlAlchemyKillSwitchStateRepository
from atp_platform.correlation import get_correlation_id, new_correlation_id

router = APIRouter(prefix="/api/v1/kill-switches", tags=["kill-switches"])


@router.get(
    "",
    response_model=KillSwitchListResponse,
    dependencies=[Depends(require_permission(Permission.READ_KILL_SWITCH))],
)
async def get_kill_switches(
    repository: Annotated[SqlAlchemyKillSwitchStateRepository, Depends(get_kill_switch_repository)],
) -> KillSwitchListResponse:
    snapshots = await kill_switches_service.list_kill_switches(repository)
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


async def _set_engaged(
    *,
    switch_id: str,
    engaged: bool,
    payload: KillSwitchMutationRequest,
    principal: AuthenticatedPrincipal,
    uow: UnitOfWork,
    clock: Clock,
    id_generator: IdGenerator,
) -> KillSwitchStateResponse:
    """Shared body for both mutation routes below - they differ only in
    the `Permission` FastAPI enforces before either ever reaches here and
    in the literal `engaged` value each passes in."""
    outcome = await kill_switches_service.set_switch_engaged(
        uow,
        switch_id_raw=switch_id,
        engaged=engaged,
        reason=payload.reason,
        changed_by=principal.user_id,
        correlation_id=get_correlation_id() or new_correlation_id(),
        clock=clock,
        id_generator=id_generator,
    )
    return KillSwitchStateResponse(
        switch_id=outcome.state.switch_id,
        engaged=outcome.state.engaged,
        updated_at=outcome.state.updated_at,
        reason=outcome.state.reason,
    )


@router.post(
    "/{switch_id}/engage",
    response_model=KillSwitchStateResponse,
    dependencies=[
        Depends(require_permission(Permission.ENGAGE_KILL_SWITCH)),
        Depends(enforce_csrf),
    ],
)
async def engage_kill_switch(
    switch_id: str,
    payload: KillSwitchMutationRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
    clock: Annotated[Clock, Depends(get_clock)],
    id_generator: Annotated[IdGenerator, Depends(get_id_generator)],
) -> KillSwitchStateResponse:
    return await _set_engaged(
        switch_id=switch_id,
        engaged=True,
        payload=payload,
        principal=principal,
        uow=uow,
        clock=clock,
        id_generator=id_generator,
    )


@router.post(
    "/{switch_id}/disengage",
    response_model=KillSwitchStateResponse,
    dependencies=[
        Depends(require_permission(Permission.DISENGAGE_KILL_SWITCH)),
        Depends(enforce_csrf),
    ],
)
async def disengage_kill_switch(
    switch_id: str,
    payload: KillSwitchMutationRequest,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
    clock: Annotated[Clock, Depends(get_clock)],
    id_generator: Annotated[IdGenerator, Depends(get_id_generator)],
) -> KillSwitchStateResponse:
    return await _set_engaged(
        switch_id=switch_id,
        engaged=False,
        payload=payload,
        principal=principal,
        uow=uow,
        clock=clock,
        id_generator=id_generator,
    )
