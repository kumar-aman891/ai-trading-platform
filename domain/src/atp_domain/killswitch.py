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

#: ADR-007's "Clearable in Phase 1" column, as code: `GLOBAL_LIVE` and
#: `LIVE_ACCOUNT` are both "No - no route exists to clear it" and must stay
#: that way - no route, no permission, and no repository write path in this
#: codebase may ever act on either while relying on this constant to decide
#: what is mutable. The single source of truth for "is this scope
#: administratively clearable" lives here, not duplicated at the API layer.
MUTABLE_SWITCH_SCOPES: frozenset[SwitchScope] = frozenset(
    {SwitchScope.PAPER, SwitchScope.STRATEGY, SwitchScope.INSTRUMENT, SwitchScope.API_EXECUTION}
)


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

    @classmethod
    def parse(cls, raw: str) -> SwitchId:
        """The exact inverse of `__str__`: `"PAPER"` -> `SwitchId(PAPER)`,
        `"STRATEGY:abc"` -> `SwitchId(STRATEGY, "abc")`. Splits on the
        first `:` only (`partition`, not `split`), so a qualifier
        containing its own `:` round-trips - matching `__str__`, which
        never escapes one. An unrecognized scope raises
        `InvalidSwitchIdError` (not `ValueError`, so every switch-ID
        failure - unknown scope or bad qualifier shape - is the same
        exception type, mapped by `atp_api.errors` the same way)."""
        scope_part, _sep, qualifier_part = raw.partition(":")
        try:
            scope = SwitchScope(scope_part)
        except ValueError as exc:
            raise InvalidSwitchIdError(f"{scope_part!r} is not a known switch scope.") from exc
        return cls(scope=scope, qualifier=qualifier_part or None)


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
