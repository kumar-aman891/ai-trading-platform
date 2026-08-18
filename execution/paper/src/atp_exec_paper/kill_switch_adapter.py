"""DB -> domain kill-switch state adapter (ADR-007).

Maps the persistence-layer `KillSwitchStateSnapshot` rows onto
`atp_domain.killswitch.SwitchState`, fail-closed in every case that is not
a confirmed, successfully-read `ENGAGED`/`DISENGAGED` row:

- `engaged = true`  -> `SwitchState.ENGAGED`
- `engaged = false` -> `SwitchState.DISENGAGED`
- no row for a given `SwitchId`   -> `SwitchState.UNAVAILABLE` (already the
  default `atp_domain.killswitch.resolve_switch_state` falls back to for
  any `SwitchId` absent from the mapping this module builds - nothing here
  needs to special-case "missing" itself)
- the read itself fails (DB unreachable, etc.) -> `SwitchState.UNAVAILABLE`
  (this module returns an empty mapping, which resolves to UNAVAILABLE for
  every `SwitchId` by the same default)

This is what finally makes `atp_domain.risk.catalog.mode_rules.PaperKillSwitchRule`
reachable from real stored state instead of a test-supplied dict.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from atp_domain.errors import InvalidSwitchIdError
from atp_domain.killswitch import SwitchId, SwitchScope, SwitchState
from atp_persistence.repositories import (
    KillSwitchStateSnapshot,
    SqlAlchemyKillSwitchStateRepository,
)
from atp_platform.logging import get_logger

_logger = get_logger("atp_exec_paper.kill_switch_adapter")


def _parse_switch_id(raw: str) -> SwitchId | None:
    scope_str, _, qualifier = raw.partition(":")
    try:
        scope = SwitchScope(scope_str)
    except ValueError:
        return None
    try:
        return SwitchId(scope=scope, qualifier=qualifier or None)
    except InvalidSwitchIdError:
        return None


def build_kill_switch_states(
    snapshots: Sequence[KillSwitchStateSnapshot],
) -> dict[SwitchId, SwitchState]:
    """Pure mapping from stored rows to domain `SwitchState`. An
    unparseable `switch_id` string is skipped, not raised - it is exactly
    the "state could not be determined for this switch" case
    `resolve_switch_state` already treats as `UNAVAILABLE` by omission."""
    states: dict[SwitchId, SwitchState] = {}
    for snapshot in snapshots:
        switch_id = _parse_switch_id(snapshot.switch_id)
        if switch_id is None:
            _logger.warning("unparseable_kill_switch_id", switch_id=snapshot.switch_id)
            continue
        states[switch_id] = SwitchState.ENGAGED if snapshot.engaged else SwitchState.DISENGAGED
    return states


async def load_kill_switch_states(
    kill_switches: SqlAlchemyKillSwitchStateRepository,
) -> Mapping[SwitchId, SwitchState]:
    """Fail-closed at the read boundary: any exception reading kill-switch
    state (DB unreachable, etc.) yields an empty mapping - `RuleContext`
    then has no entry for any `SwitchId`, and `resolve_switch_state`
    resolves every one of them to `UNAVAILABLE`, which
    `PaperKillSwitchRule` treats as blocking, identically to an explicitly
    `ENGAGED` switch. This is the only place in `atp_exec_paper` that
    catches a broad exception - deliberately, because the failure mode it
    guards against (a state read failing) must never propagate as "assume
    disengaged"."""
    try:
        snapshots = await kill_switches.list_all()
    except Exception as exc:
        _logger.error(
            "kill_switch_state_read_failed_failing_closed",
            exc_class=exc.__class__.__name__,
        )
        return {}
    return build_kill_switch_states(snapshots)
