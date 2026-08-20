"""RBAC: the central Role -> Permission model.

Specifically proves the Phase 1 Step 8 task's core safety claim: no
`Permission` constant implies live execution, and `live_trader` is granted
nothing beyond what `paper_trader` already has (Phase 1 Step 10 widened
`paper_trader`'s own permission set with `_PAPER_TRADING_PERMISSIONS` -
`live_trader` widened with it, in lockstep, never ahead of it).
"""

from __future__ import annotations

import pytest

from atp_api.security.rbac import (
    ROLE_ADMINISTRATOR,
    ROLE_LIVE_TRADER,
    ROLE_PAPER_TRADER,
    ROLE_PERMISSIONS,
    ROLE_RESEARCHER,
    ROLE_VIEWER,
    VALID_ROLES,
    Permission,
    has_permission,
)


def test_valid_roles_match_the_core_users_check_constraint() -> None:
    assert {
        "viewer",
        "researcher",
        "paper_trader",
        "live_trader",
        "administrator",
    } == VALID_ROLES


def test_every_role_has_an_entry_in_role_permissions() -> None:
    assert set(ROLE_PERMISSIONS) == VALID_ROLES


def test_viewer_has_only_read_system() -> None:
    assert ROLE_PERMISSIONS[ROLE_VIEWER] == {Permission.READ_SYSTEM}


@pytest.mark.parametrize("permission", list(Permission))
def test_no_permission_name_implies_live_execution(permission: Permission) -> None:
    forbidden_substrings = ("LIVE", "EXECUTE", "BROKER")
    assert not any(term in permission.value for term in forbidden_substrings)


def test_live_trader_has_no_permission_beyond_paper_trader() -> None:
    """The Phase 1 Step 8 task's central safety claim, carried forward by
    Step 10: `live_trader` is a valid, assignable role, but grants zero
    additional capability over `paper_trader` - because no live route or
    live execution service exists in Phase 1 for any permission to
    authorize. Submitting a PAPER proposal (Step 10, ADR-012) is not a
    live-execution capability, so widening `paper_trader` with it widened
    `live_trader` identically, never ahead of it."""
    assert ROLE_PERMISSIONS[ROLE_LIVE_TRADER] == ROLE_PERMISSIONS[ROLE_PAPER_TRADER]


def test_researcher_has_strictly_less_than_paper_trader_and_live_trader() -> None:
    """`researcher` deliberately does not receive Step 10's PAPER-trading
    permission set - it can look up instruments (research) but not submit
    a proposal or read the paper ledger (trading)."""
    assert ROLE_PERMISSIONS[ROLE_RESEARCHER] < ROLE_PERMISSIONS[ROLE_PAPER_TRADER]
    assert ROLE_PERMISSIONS[ROLE_RESEARCHER] < ROLE_PERMISSIONS[ROLE_LIVE_TRADER]


def test_administrator_has_strictly_more_than_the_trading_roles() -> None:
    assert ROLE_PERMISSIONS[ROLE_LIVE_TRADER] < ROLE_PERMISSIONS[ROLE_ADMINISTRATOR]
    assert ROLE_PERMISSIONS[ROLE_PAPER_TRADER] < ROLE_PERMISSIONS[ROLE_ADMINISTRATOR]


def test_has_permission_true_for_a_granted_permission() -> None:
    assert has_permission(ROLE_ADMINISTRATOR, Permission.MANAGE_USERS) is True


def test_has_permission_false_for_an_ungranted_permission() -> None:
    assert has_permission(ROLE_VIEWER, Permission.READ_AUDIT) is False


def test_has_permission_false_for_an_unknown_role() -> None:
    """A client-supplied or corrupted role string is never silently
    granted anything - server-side RBAC fails closed."""
    assert has_permission("not-a-real-role", Permission.READ_SYSTEM) is False


def test_no_role_grants_every_permission_except_administrator() -> None:
    non_admin_roles = VALID_ROLES - {ROLE_ADMINISTRATOR}
    all_permissions = set(Permission)
    for role in non_admin_roles:
        assert ROLE_PERMISSIONS[role] != all_permissions


# --- kill-switch engage/disengage asymmetry (Phase 1 Step 14, ADR-007) --


@pytest.mark.parametrize("role", [ROLE_PAPER_TRADER, ROLE_LIVE_TRADER, ROLE_ADMINISTRATOR])
def test_engage_kill_switch_is_granted_to_every_trading_capable_role_and_administrator(
    role: str,
) -> None:
    assert has_permission(role, Permission.ENGAGE_KILL_SWITCH) is True


@pytest.mark.parametrize("role", [ROLE_VIEWER, ROLE_RESEARCHER])
def test_engage_kill_switch_is_not_granted_below_paper_trader(role: str) -> None:
    assert has_permission(role, Permission.ENGAGE_KILL_SWITCH) is False


@pytest.mark.parametrize(
    "role", [ROLE_VIEWER, ROLE_RESEARCHER, ROLE_PAPER_TRADER, ROLE_LIVE_TRADER]
)
def test_disengage_kill_switch_is_granted_to_administrator_only(role: str) -> None:
    """ADR-007's deliberate asymmetry: every role below `administrator` -
    including `paper_trader`/`live_trader`, which can engage - must not be
    able to disengage. 'Stopping is cheaper than starting.'"""
    assert has_permission(role, Permission.DISENGAGE_KILL_SWITCH) is False


def test_administrator_can_both_engage_and_disengage_kill_switches() -> None:
    assert has_permission(ROLE_ADMINISTRATOR, Permission.ENGAGE_KILL_SWITCH) is True
    assert has_permission(ROLE_ADMINISTRATOR, Permission.DISENGAGE_KILL_SWITCH) is True


def test_paper_trader_can_engage_but_not_disengage() -> None:
    """The exact scenario ADR-007 names: `paper_trader` may engage a kill
    switch but may not clear one."""
    assert has_permission(ROLE_PAPER_TRADER, Permission.ENGAGE_KILL_SWITCH) is True
    assert has_permission(ROLE_PAPER_TRADER, Permission.DISENGAGE_KILL_SWITCH) is False
