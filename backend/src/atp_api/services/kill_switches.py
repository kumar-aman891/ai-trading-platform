"""`GET /api/v1/kill-switches` and `POST .../engage`/`.../disengage`
application logic (Phase 1 Step 7 read; Phase 1 Step 14 mutation,
ADR-007).

The read path (`list_kill_switches`) returns exactly what
`core.kill_switch_state` currently holds - interpreting a switch's
blocking-ness (the three-state ENGAGED/DISENGAGED/UNAVAILABLE fail-closed
policy in `atp_domain.killswitch`) is deliberately not performed here,
that logic belongs to whatever component actually gates an order on it
(`atp_exec_paper.kill_switch_adapter`), not to a read-only status display.

The mutation path (`set_switch_engaged`) is the one place `atp_api`
decides *which* switches may be administratively mutated at all -
`atp_domain.killswitch.MUTABLE_SWITCH_SCOPES` excludes `GLOBAL_LIVE`/
`LIVE_ACCOUNT` by construction (ADR-007: neither is clearable in Phase 1),
and this function is the only caller of
`SqlAlchemyKillSwitchStateRepository.apply_transition` in this codebase -
the repository itself takes no position on which scopes are mutable, so
this check is what actually enforces the boundary before any write is
even attempted.

Every audit event this function writes shares the caller's `UnitOfWork`
transaction (ADR-010) with the state change it records - a state change
and its audit event either both persist or neither does, the same
guarantee `atp_api.services.auth` already provides for login/logout.
"""

from __future__ import annotations

from dataclasses import dataclass

from atp_api.errors import ForbiddenError
from atp_domain.audit import (
    ACTION_KILL_SWITCH_DISENGAGED,
    ACTION_KILL_SWITCH_ENGAGED,
    AuditEvent,
)
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.killswitch import MUTABLE_SWITCH_SCOPES, SwitchId, SwitchScope
from atp_domain.types import ActorType, EventId, InstrumentId, StrategyId
from atp_persistence.db import UnitOfWork
from atp_persistence.repositories import (
    KillSwitchStateSnapshot,
    SqlAlchemyKillSwitchStateRepository,
)


async def list_kill_switches(
    repository: SqlAlchemyKillSwitchStateRepository,
) -> list[KillSwitchStateSnapshot]:
    return list(await repository.list_all())


@dataclass(frozen=True, slots=True)
class SwitchTransitionOutcome:
    state: KillSwitchStateSnapshot
    changed: bool


def _parse_mutable_switch_id(raw: str) -> SwitchId:
    """`SwitchId.parse` alone only proves `raw` names one of the six
    ADR-007 scopes - it has no opinion on which of those six may be
    administratively mutated (that is `MUTABLE_SWITCH_SCOPES`'s job, one
    layer up, deliberately kept out of the domain parser so a future
    read-only consumer of `SwitchId.parse` - logging, a future admin
    listing - is not forced to also carry a mutability opinion it does
    not need). A syntactically valid but immutable scope (`GLOBAL_LIVE`,
    `LIVE_ACCOUNT`) is rejected here with `ForbiddenError` (403), not the
    `DomainError` -> 422 path `SwitchId.parse` itself would raise for a
    genuinely malformed ID - the ID is well-formed, the *action* is what
    is forbidden."""
    switch_id = SwitchId.parse(raw)
    if switch_id.scope not in MUTABLE_SWITCH_SCOPES:
        raise ForbiddenError(f"{switch_id.scope.value} cannot be mutated in Phase 1 (ADR-007).")
    return switch_id


def _qualified_audit_ids(switch_id: SwitchId) -> tuple[StrategyId | None, InstrumentId | None]:
    """`(strategy_id, instrument_id)` for the `AuditEvent` this switch's
    transition writes - populated only for the one scope each applies to,
    both `None` for `PAPER`/`API_EXECUTION`. A legitimate use of fields
    `AuditEvent` already defines, not new schema: a `STRATEGY:{id}`
    transition genuinely is strategy-scoped context."""
    if switch_id.scope is SwitchScope.STRATEGY and switch_id.qualifier is not None:
        return StrategyId(switch_id.qualifier), None
    if switch_id.scope is SwitchScope.INSTRUMENT and switch_id.qualifier is not None:
        return None, InstrumentId(switch_id.qualifier)
    return None, None


async def set_switch_engaged(
    uow: UnitOfWork,
    *,
    switch_id_raw: str,
    engaged: bool,
    reason: str,
    changed_by: str,
    correlation_id: str,
    clock: Clock,
    id_generator: IdGenerator,
) -> SwitchTransitionOutcome:
    """Route-agnostic core of both `POST .../engage` and `.../disengage` -
    the two routes differ only in which `Permission` they require and
    which `engaged` value they pass here. Permission enforcement itself
    already happened in `atp_api.deps.require_permission` before this
    function is ever called (ADR-007's engage/disengage asymmetry is a
    `Permission` question, not something re-checked here) - this function
    enforces only the *scope* boundary (`_parse_mutable_switch_id`),
    which no `Permission` can express (`atp_api.security.rbac`'s own
    module docstring: a permission authorizes an *action*, not a
    *target* - "may this role ENGAGE_KILL_SWITCH" says nothing about
    *which* switch, the same way `SUBMIT_PAPER_PROPOSAL` says nothing
    about which instrument)."""
    switch_id = _parse_mutable_switch_id(switch_id_raw)
    switch_id_str = str(switch_id)

    current = await uow.kill_switches.get_current(switch_id_str)
    if current is not None and current.engaged == engaged:
        return SwitchTransitionOutcome(state=current, changed=False)

    now = clock.now()
    event_id = EventId(id_generator.new_id())
    strategy_id, instrument_id = _qualified_audit_ids(switch_id)
    await uow.audit.save(
        AuditEvent(
            event_id=event_id,
            correlation_id=correlation_id,
            occurred_at=now,
            recorded_at=now,
            actor_type=ActorType.USER,
            actor_id=changed_by,
            action=ACTION_KILL_SWITCH_ENGAGED if engaged else ACTION_KILL_SWITCH_DISENGAGED,
            mode=None,
            strategy_id=strategy_id,
            strategy_version=None,
            instrument_id=instrument_id,
            source_refs={"switch_id": switch_id_str, "reason": reason},
            decision="APPROVED",
        )
    )
    new_state = await uow.kill_switches.apply_transition(
        switch_id_str,
        new_engaged=engaged,
        changed_by=changed_by,
        reason=reason,
        now=now,
        history_id=id_generator.new_id(),
        audit_event_id=event_id,
    )
    return SwitchTransitionOutcome(state=new_state, changed=True)
