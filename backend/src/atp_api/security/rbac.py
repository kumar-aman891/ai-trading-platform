"""Central permission model (Phase 1 Step 8).

`Role -> Permission` is a pure, in-memory mapping - no route or service
anywhere checks a role string directly (`docs/SECURITY.md`: "Create
distinct permissions"). Every protected route declares the `Permission` it
requires via `atp_api.deps.require_permission`; there is no scattered
`if role == "administrator"` anywhere in this codebase.

ROLE NAMES ARE NEVER A SUFFICIENT AUTHORIZATION CHECK BY THEMSELVES.
`Permission` is the sole authoritative authorization unit. A role is only
ever a *label a user carries*; the only question any route/service is
permitted to ask is "does this role's permission set contain the
permission this action requires" (`has_permission`, below) - never
"is this role X". Comparing a role string directly anywhere outside this
module's own `ROLE_PERMISSIONS` table definition is exactly the scattered
`if role == "administrator"` pattern the module docstring above says does
not exist in this codebase, and any new authorization check must go
through `has_permission`/`require_permission`, not a role comparison.

The five roles below (`viewer`, `researcher`, `paper_trader`,
`live_trader`, `administrator`) are the full set of *future capability
categories* the platform's RBAC design anticipates across every phase, not
a Phase-1-only vocabulary - `docs/SECURITY.md`/`docs/schemas/user.md`
define them ahead of the capabilities some of them will eventually
authorize, the same way `atp_domain.types.Mode.LIVE` is a value the type
system can represent years before any code path is permitted to act on
it. Declaring the role now and gating what it can *do* separately (via
`ROLE_PERMISSIONS`) is what lets `core.users.role` and its CHECK
constraint stay stable as later phases add capability, instead of
requiring a schema migration every time a new permission is introduced.

`live_trader` is one such future-capability role: it is valid and
assignable in Phase 1 (`docs/schemas/user.md`), but in Phase 1 it has
**zero** live-trading capability - `ROLE_PERMISSIONS[ROLE_LIVE_TRADER]` is
*exactly* `ROLE_PERMISSIONS[ROLE_RESEARCHER]` and
`ROLE_PERMISSIONS[ROLE_PAPER_TRADER]` (asserted directly by
`tests/unit/security/test_rbac.py`), because no live route or live
execution service exists for any permission to authorize (ADR-005,
ADR-006, ADR-008). No Phase-1 permission grants live execution - see
`Permission`'s own docstring below for the enum-level guarantee this rests
on. Widening `live_trader`'s permissions, or introducing a permission that
authorizes something live-execution-shaped, is a Phase 4+ decision gated
on a live execution gateway actually existing, not a Step 8 one.
"""

from __future__ import annotations

from enum import StrEnum

# Matches `core.users.role`'s CHECK constraint exactly
# (persistence/src/atp_persistence/models/core.py, docs/schemas/user.md).
# See the module docstring above: these are the full set of future
# capability categories the RBAC design anticipates, not a Phase-1-only
# vocabulary - what each role can actually *do* in Phase 1 is entirely
# determined by ROLE_PERMISSIONS below, never by the role name itself.
ROLE_VIEWER = "viewer"
ROLE_RESEARCHER = "researcher"
ROLE_PAPER_TRADER = "paper_trader"
ROLE_LIVE_TRADER = "live_trader"
ROLE_ADMINISTRATOR = "administrator"

VALID_ROLES: frozenset[str] = frozenset(
    {ROLE_VIEWER, ROLE_RESEARCHER, ROLE_PAPER_TRADER, ROLE_LIVE_TRADER, ROLE_ADMINISTRATOR}
)


class Permission(StrEnum):
    """Deliberately small in Phase 1 - one permission per route this step
    actually protects, plus two administrator-only permissions the schema
    design anticipates (`MANAGE_USERS`, `MANAGE_SECURITY`) but no Phase 1
    route yet exercises.

    No Phase-1 permission grants live execution. No `EXECUTE_LIVE`/
    `MANAGE_LIVE_*`/any live-trading-shaped constant exists anywhere in
    this enum - there is nothing for any role, including `administrator`
    or `live_trader`, to be granted that would activate live trading, and
    `tests/safety/test_rbac_server_side.py` mechanically asserts that no
    member of this enum's value ever contains "LIVE" or "EXECUTE"."""

    READ_SYSTEM = "READ_SYSTEM"
    READ_AUDIT = "READ_AUDIT"
    READ_KILL_SWITCH = "READ_KILL_SWITCH"
    MANAGE_USERS = "MANAGE_USERS"
    MANAGE_SECURITY = "MANAGE_SECURITY"


_READ_ONLY_OBSERVER_PERMISSIONS: frozenset[Permission] = frozenset(
    {Permission.READ_SYSTEM, Permission.READ_AUDIT, Permission.READ_KILL_SWITCH}
)

# `ROLE_LIVE_TRADER` is deliberately the *same object* as
# `ROLE_RESEARCHER`/`ROLE_PAPER_TRADER`'s permission set below, not merely
# an equal-by-value copy - there is exactly one "Phase 1 observer"
# permission set in this table, and live_trader is a member of it like any
# other non-administrator role, never a distinct, wider one.
ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    ROLE_VIEWER: frozenset({Permission.READ_SYSTEM}),
    ROLE_RESEARCHER: _READ_ONLY_OBSERVER_PERMISSIONS,
    ROLE_PAPER_TRADER: _READ_ONLY_OBSERVER_PERMISSIONS,
    ROLE_LIVE_TRADER: _READ_ONLY_OBSERVER_PERMISSIONS,
    ROLE_ADMINISTRATOR: _READ_ONLY_OBSERVER_PERMISSIONS
    | {Permission.MANAGE_USERS, Permission.MANAGE_SECURITY},
}


def has_permission(role: str, permission: Permission) -> bool:
    """The one authoritative authorization question this module answers.
    Every route/service must call this (directly, or via
    `atp_api.deps.require_permission`) rather than inspecting `role`
    itself - a role string is an identity label, not an authorization
    decision (see this module's docstring)."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
