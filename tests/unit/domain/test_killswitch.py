"""Tests for atp_domain.killswitch - fail-closed semantics, all six switch
scopes, the three-way engaged/disengaged/unavailable distinction."""

from __future__ import annotations

import pytest

from atp_domain.errors import InvalidSwitchIdError
from atp_domain.killswitch import (
    MUTABLE_SWITCH_SCOPES,
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


# --- SwitchId.parse (Phase 1 Step 14, ADR-007) ---------------------------


@pytest.mark.parametrize(
    "scope",
    [
        SwitchScope.GLOBAL_LIVE,
        SwitchScope.LIVE_ACCOUNT,
        SwitchScope.PAPER,
        SwitchScope.API_EXECUTION,
    ],
)
def test_parse_round_trips_unqualified_scopes(scope: SwitchScope) -> None:
    switch_id = SwitchId.parse(scope.value)
    assert switch_id == SwitchId(scope)
    assert str(switch_id) == scope.value


@pytest.mark.parametrize("scope", [SwitchScope.STRATEGY, SwitchScope.INSTRUMENT])
def test_parse_round_trips_qualified_scopes(scope: SwitchScope) -> None:
    raw = f"{scope.value}:momentum-v1"
    switch_id = SwitchId.parse(raw)
    assert switch_id == SwitchId(scope, qualifier="momentum-v1")
    assert str(switch_id) == raw


def test_parse_keeps_everything_after_the_first_colon_as_the_qualifier() -> None:
    """`__str__` never escapes a colon inside a qualifier - `parse` must
    be its exact inverse, so a qualifier containing one still round-trips."""
    switch_id = SwitchId.parse("INSTRUMENT:NSE:INFY")
    assert switch_id.qualifier == "NSE:INFY"
    assert str(switch_id) == "INSTRUMENT:NSE:INFY"


def test_parse_rejects_an_unknown_scope() -> None:
    with pytest.raises(InvalidSwitchIdError, match="not a known switch scope"):
        SwitchId.parse("NOT_A_REAL_SCOPE")


def test_parse_rejects_an_unqualified_scope_given_a_qualifier() -> None:
    """`parse` delegates to `__post_init__` for shape validation - it does
    not duplicate the qualifier-presence rule itself."""
    with pytest.raises(InvalidSwitchIdError, match="must not carry a qualifier"):
        SwitchId.parse("PAPER:anything")


def test_parse_rejects_a_qualified_scope_given_no_qualifier() -> None:
    with pytest.raises(InvalidSwitchIdError, match="requires a qualifier"):
        SwitchId.parse("STRATEGY")


# --- MUTABLE_SWITCH_SCOPES (ADR-007's "Clearable in Phase 1" column) ----


def test_mutable_switch_scopes_excludes_global_live_and_live_account() -> None:
    assert SwitchScope.GLOBAL_LIVE not in MUTABLE_SWITCH_SCOPES
    assert SwitchScope.LIVE_ACCOUNT not in MUTABLE_SWITCH_SCOPES


def test_mutable_switch_scopes_includes_exactly_the_four_clearable_scopes() -> None:
    assert {
        SwitchScope.PAPER,
        SwitchScope.STRATEGY,
        SwitchScope.INSTRUMENT,
        SwitchScope.API_EXECUTION,
    } == MUTABLE_SWITCH_SCOPES
