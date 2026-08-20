"""`atp_strategy.kill_switch_adapter` - the DB -> domain kill-switch state
mapping, fail-closed to UNAVAILABLE in every case except a confirmed,
successfully-read ENGAGED/DISENGAGED row. Mirrors
`tests/unit/exec_paper/test_kill_switch_adapter.py`'s coverage exactly,
since the two adapters are deliberately identical logic (ADR-014 §F)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from atp_domain.killswitch import SwitchId, SwitchScope, SwitchState, resolve_switch_state
from atp_persistence.repositories import KillSwitchStateSnapshot
from atp_strategy.kill_switch_adapter import build_kill_switch_states, load_kill_switch_states

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FakeKillSwitchStateRepository:
    def __init__(
        self,
        snapshots: list[KillSwitchStateSnapshot] | None = None,
        *,
        raise_on_read: bool = False,
    ) -> None:
        self._snapshots = snapshots or []
        self._raise_on_read = raise_on_read

    async def list_all(self) -> list[KillSwitchStateSnapshot]:
        if self._raise_on_read:
            raise ConnectionError("simulated kill-switch read failure")
        return self._snapshots


def _strategy_snapshot(
    *, engaged: bool, strategy_key: str = "momentum-v1"
) -> KillSwitchStateSnapshot:
    return KillSwitchStateSnapshot(
        switch_id=f"STRATEGY:{strategy_key}",
        engaged=engaged,
        updated_at=_NOW,
        updated_by=None,
        reason="test" if engaged else None,
    )


def test_engaged_strategy_snapshot_maps_to_engaged_state() -> None:
    states = build_kill_switch_states([_strategy_snapshot(engaged=True)])
    assert states[SwitchId(SwitchScope.STRATEGY, "momentum-v1")] is SwitchState.ENGAGED


def test_disengaged_strategy_snapshot_maps_to_disengaged_state() -> None:
    states = build_kill_switch_states([_strategy_snapshot(engaged=False)])
    assert states[SwitchId(SwitchScope.STRATEGY, "momentum-v1")] is SwitchState.DISENGAGED


def test_missing_strategy_switch_resolves_to_unavailable_via_domain_default() -> None:
    """The core human-in-the-loop control (ADR-014 §B): a strategy that has
    never been administratively DISENGAGED has no row at all, and must
    resolve to UNAVAILABLE - blocking, identically to ENGAGED."""
    states = build_kill_switch_states([])
    resolved = resolve_switch_state(SwitchId(SwitchScope.STRATEGY, "never-enabled"), states)
    assert resolved is SwitchState.UNAVAILABLE


def test_unparseable_switch_id_is_skipped_not_raised() -> None:
    bogus = KillSwitchStateSnapshot(
        switch_id="NOT_A_REAL_SCOPE", engaged=True, updated_at=_NOW, updated_by=None, reason=None
    )
    states = build_kill_switch_states([bogus])
    assert states == {}


def test_read_failure_yields_empty_mapping_failing_closed() -> None:
    async def run() -> None:
        repo = _FakeKillSwitchStateRepository(raise_on_read=True)
        states = await load_kill_switch_states(repo)
        assert states == {}
        assert (
            resolve_switch_state(SwitchId(SwitchScope.STRATEGY, "momentum-v1"), states)
            is SwitchState.UNAVAILABLE
        )

    asyncio.run(run())


def test_successful_read_yields_populated_mapping() -> None:
    async def run() -> None:
        repo = _FakeKillSwitchStateRepository(snapshots=[_strategy_snapshot(engaged=False)])
        states = await load_kill_switch_states(repo)
        assert states[SwitchId(SwitchScope.STRATEGY, "momentum-v1")] is SwitchState.DISENGAGED

    asyncio.run(run())


def test_no_state_mutation_method_exists_on_the_adapter_module() -> None:
    """ADR-014 §F: this module only ever reads kill-switch state - no
    engage/disengage/apply_transition function is defined here."""
    import atp_strategy.kill_switch_adapter as adapter

    public_names = {name for name in dir(adapter) if not name.startswith("_")}
    mutating_names = {
        name for name in public_names if "engage" in name.lower() or "apply" in name.lower()
    }
    assert mutating_names == set()
