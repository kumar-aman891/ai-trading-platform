"""RBAC: the central Role -> Permission model.

Specifically proves the Phase 1 Step 8 task's core safety claim: no
`Permission` constant implies live execution, and `live_trader` is granted
nothing beyond what `researcher`/`paper_trader` already have.
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


def test_live_trader_has_no_permission_beyond_researcher_and_paper_trader() -> None:
    """The Phase 1 Step 8 task's central safety claim: `live_trader` is a
    valid, assignable role, but grants zero additional capability over the
    other non-administrator observer roles - because no live route or live
    execution service exists in Phase 1 for any permission to authorize."""
    assert ROLE_PERMISSIONS[ROLE_LIVE_TRADER] == ROLE_PERMISSIONS[ROLE_RESEARCHER]
    assert ROLE_PERMISSIONS[ROLE_LIVE_TRADER] == ROLE_PERMISSIONS[ROLE_PAPER_TRADER]


def test_administrator_has_strictly_more_than_the_observer_roles() -> None:
    assert ROLE_PERMISSIONS[ROLE_LIVE_TRADER] < ROLE_PERMISSIONS[ROLE_ADMINISTRATOR]


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
