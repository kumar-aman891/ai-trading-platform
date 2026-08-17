"""Tests for atp_domain.killswitch - fail-closed semantics, all six switch
scopes, the three-way engaged/disengaged/unavailable distinction."""

from __future__ import annotations

import pytest

from atp_domain.errors import InvalidSwitchIdError
from atp_domain.killswitch import (
    SwitchId,
    SwitchScope,
    SwitchState,
    is_blocking,
    resolve_switch_state,
)


@pytest.mark.parametrize(
    "scope",
    [
        SwitchScope.GLOBAL_LIVE,
        SwitchScope.LIVE_ACCOUNT,
        SwitchScope.PAPER,
        SwitchScope.API_EXECUTION,
    ],
)
def test_unqualified_scopes_construct_without_a_qualifier(scope: SwitchScope) -> None:
    switch_id = SwitchId(scope)
    assert switch_id.qualifier is None


@pytest.mark.parametrize("scope", [SwitchScope.GLOBAL_LIVE, SwitchScope.PAPER])
def test_unqualified_scopes_reject_a_qualifier(scope: SwitchScope) -> None:
    with pytest.raises(InvalidSwitchIdError, match="must not carry a qualifier"):
        SwitchId(scope, qualifier="anything")


@pytest.mark.parametrize("scope", [SwitchScope.STRATEGY, SwitchScope.INSTRUMENT])
def test_qualified_scopes_require_a_qualifier(scope: SwitchScope) -> None:
    with pytest.raises(InvalidSwitchIdError, match="requires a qualifier"):
        SwitchId(scope)


@pytest.mark.parametrize("scope", [SwitchScope.STRATEGY, SwitchScope.INSTRUMENT])
def test_qualified_scopes_construct_with_a_qualifier(scope: SwitchScope) -> None:
    switch_id = SwitchId(scope, qualifier="momentum-v1")
    assert switch_id.qualifier == "momentum-v1"


def test_all_six_switch_scopes_are_defined() -> None:
    assert {member.value for member in SwitchScope} == {
        "GLOBAL_LIVE",
        "LIVE_ACCOUNT",
        "PAPER",
        "STRATEGY",
        "INSTRUMENT",
        "API_EXECUTION",
    }


def test_resolve_switch_state_missing_entry_is_unavailable() -> None:
    switch_id = SwitchId(SwitchScope.PAPER)
    assert resolve_switch_state(switch_id, {}) is SwitchState.UNAVAILABLE


def test_resolve_switch_state_honours_present_entry() -> None:
    switch_id = SwitchId(SwitchScope.PAPER)
    states = {switch_id: SwitchState.DISENGAGED}
    assert resolve_switch_state(switch_id, states) is SwitchState.DISENGAGED


@pytest.mark.parametrize(
    ("state", "expected_blocking"),
    [
        (SwitchState.ENGAGED, True),
        (SwitchState.UNAVAILABLE, True),
        (SwitchState.DISENGAGED, False),
    ],
)
def test_is_blocking_distinguishes_all_three_states(
    state: SwitchState, expected_blocking: bool
) -> None:
    assert is_blocking(state) is expected_blocking


def test_unavailable_state_fails_closed_end_to_end() -> None:
    """A switch nobody has ever set behaves identically to one explicitly
    engaged - this is the fail-closed guarantee, exercised through the
    full resolve -> is_blocking path."""
    switch_id = SwitchId(SwitchScope.INSTRUMENT, qualifier="NSE:INFY")
    assert is_blocking(resolve_switch_state(switch_id, {})) is True
