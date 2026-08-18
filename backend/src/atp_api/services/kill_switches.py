"""`GET /api/v1/kill-switches` application logic.

Read-only: returns exactly what `core.kill_switch_state` currently holds.
Interpreting a switch's blocking-ness (the three-state ENGAGED/DISENGAGED/
UNAVAILABLE fail-closed policy in `atp_domain.killswitch`) is deliberately
not performed here - that logic belongs to whatever future component
actually gates an order on it, not to a read-only status display.
"""

from __future__ import annotations

from collections.abc import Sequence

from atp_persistence.repositories import (
    KillSwitchStateSnapshot,
    SqlAlchemyKillSwitchStateRepository,
)


async def list_kill_switches(
    repository: SqlAlchemyKillSwitchStateRepository,
) -> Sequence[KillSwitchStateSnapshot]:
    return await repository.list_all()
