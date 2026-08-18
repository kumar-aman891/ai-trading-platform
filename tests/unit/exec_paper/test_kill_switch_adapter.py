"""`atp_exec_paper.kill_switch_adapter` - the DB -> domain kill-switch
state mapping, fail-closed to UNAVAILABLE in every case except a
confirmed, successfully-read ENGAGED/DISENGAGED row."""

from __future__ import annotations

import asyncio

from atp_domain.killswitch import SwitchId, SwitchScope, SwitchState, resolve_switch_state
from atp_exec_paper.kill_switch_adapter import build_kill_switch_states, load_kill_switch_states
from atp_persistence.repositories import KillSwitchStateSnapshot
from tests.unit.exec_paper.builders import NOW, make_paper_kill_switch_snapshot
from tests.unit.exec_paper.fakes import FakeKillSwitchStateRepository


def test_engaged_snapshot_maps_to_engaged_state() -> None:
    states = build_kill_switch_states([make_paper_kill_switch_snapshot(engaged=True)])
    assert states[SwitchId(SwitchScope.PAPER)] is SwitchState.ENGAGED


def test_disengaged_snapshot_maps_to_disengaged_state() -> None:
    states = build_kill_switch_states([make_paper_kill_switch_snapshot(engaged=False)])
    assert states[SwitchId(SwitchScope.PAPER)] is SwitchState.DISENGAGED


def test_missing_switch_resolves_to_unavailable_via_domain_default() -> None:
    states = build_kill_switch_states([])
    resolved = resolve_switch_state(SwitchId(SwitchScope.PAPER), states)
    assert resolved is SwitchState.UNAVAILABLE


def test_unparseable_switch_id_is_skipped_not_raised() -> None:
    bogus = KillSwitchStateSnapshot(
        switch_id="NOT_A_REAL_SCOPE", engaged=True, updated_at=NOW, updated_by=None, reason=None
    )
    states = build_kill_switch_states([bogus])
    assert states == {}


def test_read_failure_yields_empty_mapping_failing_closed() -> None:
    async def run() -> None:
        repo = FakeKillSwitchStateRepository(raise_on_read=True)
        states = await load_kill_switch_states(repo)
        assert states == {}
        assert resolve_switch_state(SwitchId(SwitchScope.PAPER), states) is SwitchState.UNAVAILABLE

    asyncio.run(run())


def test_successful_read_yields_populated_mapping() -> None:
    async def run() -> None:
        repo = FakeKillSwitchStateRepository(
            snapshots=[make_paper_kill_switch_snapshot(engaged=False)]
        )
        states = await load_kill_switch_states(repo)
        assert states[SwitchId(SwitchScope.PAPER)] is SwitchState.DISENGAGED

    asyncio.run(run())
