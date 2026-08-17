"""Kill switches - six scopes, fail-closed policy semantics
(docs/adr/ADR-007-kill-switch-taxonomy.md).

Three explicit states, not two: ENGAGED, DISENGAGED, and UNAVAILABLE (the
state could not be determined). UNAVAILABLE blocks exactly like ENGAGED -
only an explicit DISENGAGED permits proceeding. This is enforced by
`is_blocking()`, not by convention: there is no code path in this module
that treats a missing/unknown state as anything other than blocking.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from atp_domain.errors import InvalidSwitchIdError


class SwitchScope(StrEnum):
    GLOBAL_LIVE = "GLOBAL_LIVE"
    LIVE_ACCOUNT = "LIVE_ACCOUNT"
    PAPER = "PAPER"
    STRATEGY = "STRATEGY"
    INSTRUMENT = "INSTRUMENT"
    API_EXECUTION = "API_EXECUTION"


_QUALIFIED_SCOPES = frozenset({SwitchScope.STRATEGY, SwitchScope.INSTRUMENT})


class SwitchState(StrEnum):
    ENGAGED = "ENGAGED"
    DISENGAGED = "DISENGAGED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class SwitchId:
    """A concrete kill-switch identity. STRATEGY/INSTRUMENT scopes require
    a qualifier (e.g. a strategy or instrument id); the four fixed scopes
    must not carry one."""

    scope: SwitchScope
    qualifier: str | None = None

    def __post_init__(self) -> None:
        requires_qualifier = self.scope in _QUALIFIED_SCOPES
        if requires_qualifier and not self.qualifier:
            raise InvalidSwitchIdError(
                f"{self.scope} requires a qualifier (e.g. a strategy or instrument id)."
            )
        if not requires_qualifier and self.qualifier is not None:
            raise InvalidSwitchIdError(f"{self.scope} must not carry a qualifier.")

    def __str__(self) -> str:
        return f"{self.scope.value}:{self.qualifier}" if self.qualifier else self.scope.value


def resolve_switch_state(
    switch_id: SwitchId, states: Mapping[SwitchId, SwitchState]
) -> SwitchState:
    """Fail-closed lookup: a switch with no entry in `states` is
    UNAVAILABLE, never assumed DISENGAGED."""
    return states.get(switch_id, SwitchState.UNAVAILABLE)


def is_blocking(state: SwitchState) -> bool:
    """Only DISENGAGED permits proceeding. ENGAGED and UNAVAILABLE both
    block - this is the fail-closed invariant, expressed as code rather
    than left to caller discipline."""
    return state is not SwitchState.DISENGAGED
