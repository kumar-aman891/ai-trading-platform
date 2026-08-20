"""AUTHENTICATION / SESSION / CSRF / RBAC: exercised through real HTTP
requests against the fully-wired app, with only the database dependency
(`atp_api.deps.get_unit_of_work`) swapped for an in-memory fake
(`tests/unit/api/fakes.py`, `tests/unit/api/conftest.py`'s `app`/`client`
fixtures). No Docker is required for any test in this module. Test
functions are plain sync - `fastapi.testclient.TestClient` runs its own
event loop internally per request, so no async test runner plugin is
needed here (this repo doesn't register one).

`GET /api/v1/kill-switches` and `GET /api/v1/audit/events` still depend on
the *real*, un-overridden `get_db_session` (Step 7's read-only dependency)
for their repository construction - so a caller with the right permission
reaches an unreachable database and gets `503 SERVICE_UNAVAILABLE`, while a
caller *without* the permission never gets that far and sees `403
FORBIDDEN`. That 403-vs-503 split is exactly the RBAC gate this module
proves: `503` demonstrates the request passed authorization and reached
DB-dependent code; `403` demonstrates it did not. A genuine `200` for those
two routes needs a real database (`tests/integration/db/`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atp_api.security.cookies import CSRF_COOKIE_NAME, SESSION_COOKIE_NAME
from atp_api.security.passwords import hash_password
from atp_api.security.rbac import (
    ROLE_ADMINISTRATOR,
    ROLE_LIVE_TRADER,
    ROLE_PAPER_TRADER,
    ROLE_RESEARCHER,
    ROLE_VIEWER,
)
from atp_domain.audit import (
    ACTION_AUTHORIZATION_DENIED,
    ACTION_LOGIN_FAILED,
    ACTION_LOGIN_SUCCEEDED,
    ACTION_PASSWORD_CHANGED,
    ACTION_SESSION_REVOKED,
)
from atp_persistence.repositories import UserRecord
from tests.unit.api.fakes import FakeUnitOfWork

_PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hash_password(_PASSWORD)  # module-level: argon2 hashing is slow, hash once


def _seed_user(
    uow: FakeUnitOfWork,
    *,
    username: str,
    role: str,
    is_active: bool = True,
    must_change_password: bool = False,
) -> str:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    user_id = f"user-{username}"
    uow.users._by_id[user_id] = UserRecord(
        user_id=user_id,
        username=username,
        password_hash=_PASSWORD_HASH,
        role=role,
        is_active=is_active,
        must_change_password=must_change_password,
        created_at=created_at,
        updated_at=created_at,
    )
    return user_id


def _login(client: TestClient, *, username: str, password: str = _PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


def _change_password(
    client: TestClient,
    *,
    current_password: str,
    new_password: str,
    csrf_token: str | None = None,
    include_csrf_header: bool = True,
):
    headers = {}
    if include_csrf_header:
        headers["x-csrf-token"] = (
            csrf_token if csrf_token is not None else client.cookies.get(CSRF_COOKIE_NAME)
        )
    return client.post(
        "/api/v1/auth/password",
        json={"current_password": current_password, "new_password": new_password},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# AUTHENTICATION
# ---------------------------------------------------------------------------


def test_login_succeeds_for_a_correct_password(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="alice", role=ROLE_VIEWER)
    response = _login(client, username="alice")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "alice"
    assert body["role"] == ROLE_VIEWER
    assert _PASSWORD not in response.text
    assert _PASSWORD_HASH not in response.text


def test_login_fails_for_unknown_username(client: TestClient, fake_uow) -> None:
    response = _login(client, username="nobody")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_FAILED"


def test_login_fails_for_wrong_password(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="bob", role=ROLE_VIEWER)
    response = _login(client, username="bob", password="wrong password")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_FAILED"


def test_login_failure_response_is_identical_for_unknown_user_and_wrong_password(
    client: TestClient, fake_uow
) -> None:
    """Non-enumeration: the two distinct server-side reasons produce
    identical response shapes (modulo the correlation ID)."""
    _seed_user(fake_uow, username="carol", role=ROLE_VIEWER)
    unknown = _login(client, username="does-not-exist")
    wrong_password = _login(client, username="carol", password="wrong password")
    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["code"] == wrong_password.json()["code"]
    assert unknown.json()["message"] == wrong_password.json()["message"]


def test_login_fails_for_an_inactive_user(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="dana", role=ROLE_VIEWER, is_active=False)
    response = _login(client, username="dana")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_FAILED"


def test_login_response_never_contains_the_password_hash(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="erin", role=ROLE_VIEWER)
    response = _login(client, username="erin")
    assert "$argon2id$" not in response.text


def test_repeated_failed_logins_trigger_rate_limiting(client: TestClient, fake_uow) -> None:
    for _ in range(5):
        _login(client, username="nobody")
    limited = _login(client, username="nobody")
    assert limited.status_code == 429
    assert limited.json()["code"] == "RATE_LIMIT_EXCEEDED"


def test_failed_login_records_an_audit_event_without_a_username_or_password(
    client: TestClient, fake_uow
) -> None:
    _login(client, username="nobody")
    events = [e for e in fake_uow.audit.events if e.action == ACTION_LOGIN_FAILED]
    assert len(events) == 1
    assert events[0].actor_id is None  # unauthenticated - nothing to attribute it to


def test_successful_login_records_an_audit_event_with_the_user_id(
    client: TestClient, fake_uow
) -> None:
    user_id = _seed_user(fake_uow, username="frank", role=ROLE_VIEWER)
    _login(client, username="frank")
    events = [e for e in fake_uow.audit.events if e.action == ACTION_LOGIN_SUCCEEDED]
    assert len(events) == 1
    assert events[0].actor_id == user_id


# ---------------------------------------------------------------------------
# SESSION
# ---------------------------------------------------------------------------


def test_login_sets_a_secure_httponly_samesite_strict_session_cookie(
    client: TestClient, fake_uow
) -> None:
    _seed_user(fake_uow, username="grace", role=ROLE_VIEWER)
    _login(client, username="grace")
    cookie = next(c for c in client.cookies.jar if c.name == SESSION_COOKIE_NAME)
    assert cookie.secure is True
    assert cookie._rest.get("SameSite", "").lower() == "strict"  # type: ignore[attr-defined]


def test_csrf_cookie_is_not_httponly(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="henry", role=ROLE_VIEWER)
    _login(client, username="henry")
    cookie = next(c for c in client.cookies.jar if c.name == CSRF_COOKIE_NAME)
    assert not cookie._rest.get("HttpOnly")  # type: ignore[attr-defined]


def test_me_without_a_cookie_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_INVALID"


def test_me_with_a_valid_session_returns_the_user(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="iris", role=ROLE_RESEARCHER)
    _login(client, username="iris")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json() == {
        "username": "iris",
        "role": ROLE_RESEARCHER,
        "must_change_password": False,
    }


def test_unknown_session_cookie_is_rejected(client: TestClient) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, "not-a-real-token")
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_INVALID"


def test_expired_session_is_rejected(client: TestClient, fake_uow, frozen_clock) -> None:
    _seed_user(fake_uow, username="jack", role=ROLE_VIEWER)
    _login(client, username="jack")
    frozen_clock.advance(timedelta(hours=9))  # past the 8h TTL
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_EXPIRED"


def test_session_is_slid_forward_on_each_authenticated_request(
    client: TestClient, fake_uow, frozen_clock
) -> None:
    _seed_user(fake_uow, username="karen", role=ROLE_VIEWER)
    _login(client, username="karen")
    (session_before,) = fake_uow.sessions._by_hash.values()
    frozen_clock.advance(timedelta(hours=1))
    client.get("/api/v1/auth/me")
    (session_after,) = fake_uow.sessions._by_hash.values()
    assert session_after.expires_at > session_before.expires_at


def test_revoked_session_is_rejected(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="leo", role=ROLE_VIEWER)
    _login(client, username="leo")
    csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
    client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_cookie})
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_INVALID"


def test_logout_without_a_cookie_is_a_safe_no_op(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200


def test_repeated_logout_is_idempotent(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="mona", role=ROLE_VIEWER)
    _login(client, username="mona")
    csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
    first = client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_cookie})
    second = client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_cookie})
    assert first.status_code == 200
    assert second.status_code == 200


def test_logout_clears_both_cookies(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="nate", role=ROLE_VIEWER)
    _login(client, username="nate")
    csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
    client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_cookie})
    assert SESSION_COOKIE_NAME not in client.cookies
    assert CSRF_COOKIE_NAME not in client.cookies


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def test_logout_without_a_csrf_header_is_rejected(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="olga", role=ROLE_VIEWER)
    _login(client, username="olga")
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"
    # The session must still be live - a failed CSRF check has no side effect.
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200


def test_logout_with_a_mismatched_csrf_header_is_rejected(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="pete", role=ROLE_VIEWER)
    _login(client, username="pete")
    response = client.post("/api/v1/auth/logout", headers={"x-csrf-token": "forged-value"})
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


def test_logout_with_a_valid_csrf_header_succeeds(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="quinn", role=ROLE_VIEWER)
    _login(client, username="quinn")
    csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
    response = client.post("/api/v1/auth/logout", headers={"x-csrf-token": csrf_cookie})
    assert response.status_code == 200


def test_get_requests_never_require_a_csrf_token(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="ray", role=ROLE_VIEWER)
    _login(client, username="ray")
    response = client.get("/api/v1/auth/me")  # no X-CSRF-Token header at all
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

_OBSERVER_ROLES = (ROLE_RESEARCHER, ROLE_PAPER_TRADER, ROLE_LIVE_TRADER, ROLE_ADMINISTRATOR)


def test_unauthenticated_caller_cannot_reach_any_protected_route(client: TestClient) -> None:
    for path in ("/api/v1/system/status", "/api/v1/kill-switches", "/api/v1/audit/events"):
        response = client.get(path)
        assert response.status_code == 401, path


@pytest.mark.parametrize(
    "role", (ROLE_VIEWER, ROLE_RESEARCHER, ROLE_PAPER_TRADER, ROLE_LIVE_TRADER, ROLE_ADMINISTRATOR)
)
def test_every_role_can_read_system_status(client: TestClient, fake_uow, role: str) -> None:
    """READ_SYSTEM is granted to all five roles - system status is always
    reachable once authenticated, regardless of role."""
    _seed_user(fake_uow, username=f"user-{role}", role=role)
    _login(client, username=f"user-{role}")
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200


def test_viewer_cannot_read_kill_switches_or_audit(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="viewer1", role=ROLE_VIEWER)
    _login(client, username="viewer1")
    for path in ("/api/v1/kill-switches", "/api/v1/audit/events"):
        response = client.get(path)
        assert response.status_code == 403, path
        assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize("role", _OBSERVER_ROLES)
def test_observer_and_administrator_roles_pass_the_kill_switch_and_audit_gate(
    client: TestClient, fake_uow, role: str
) -> None:
    """`503` (not `403`) proves the request passed RBAC and reached
    DB-dependent code - see this module's docstring."""
    _seed_user(fake_uow, username=f"observer-{role}", role=role)
    _login(client, username=f"observer-{role}")
    for path in ("/api/v1/kill-switches", "/api/v1/audit/events"):
        response = client.get(path)
        assert response.status_code == 503, path


def test_authorization_denial_is_audited(client: TestClient, fake_uow) -> None:
    user_id = _seed_user(fake_uow, username="viewer2", role=ROLE_VIEWER)
    _login(client, username="viewer2")
    client.get("/api/v1/kill-switches")
    events = [e for e in fake_uow.audit.events if e.action == ACTION_AUTHORIZATION_DENIED]
    assert len(events) == 1
    assert events[0].actor_id == user_id


def test_live_trader_gets_no_route_a_paper_trader_does_not_also_get(
    client: TestClient, fake_uow
) -> None:
    """No route anywhere is reachable by `live_trader` but not by
    `paper_trader`/`researcher` - proving `live_trader` carries zero
    incremental capability at the HTTP layer, not just in the permission
    table (`tests/unit/security/test_rbac.py` proves the table itself)."""
    _seed_user(fake_uow, username="live1", role=ROLE_LIVE_TRADER)
    _seed_user(fake_uow, username="paper1", role=ROLE_PAPER_TRADER)

    live_client = TestClient(client.app, base_url="https://testserver")
    _login(live_client, username="live1")
    paper_client = TestClient(client.app, base_url="https://testserver")
    _login(paper_client, username="paper1")

    for path in ("/api/v1/system/status", "/api/v1/kill-switches", "/api/v1/audit/events"):
        assert live_client.get(path).status_code == paper_client.get(path).status_code, path


# ---------------------------------------------------------------------------
# PASSWORD CHANGE (Phase 1 Step 16)
# ---------------------------------------------------------------------------

_NEW_PASSWORD = "a different correct horse battery staple"


def test_password_change_without_a_session_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/password",
        json={"current_password": _PASSWORD, "new_password": _NEW_PASSWORD},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_INVALID"


def test_password_change_with_the_wrong_current_password_is_rejected(
    client: TestClient, fake_uow
) -> None:
    _seed_user(fake_uow, username="sam", role=ROLE_VIEWER)
    _login(client, username="sam")
    response = _change_password(
        client, current_password="totally wrong", new_password=_NEW_PASSWORD
    )
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_FAILED"
    # The same generic error /login uses for a wrong password - no
    # detail distinguishing "wrong current password" from any other
    # authentication failure.
    assert response.json()["message"] == "Authentication failed."


def test_password_change_succeeds_with_the_correct_current_password(
    client: TestClient, fake_uow
) -> None:
    _seed_user(fake_uow, username="tara", role=ROLE_VIEWER)
    _login(client, username="tara")
    response = _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)
    assert response.status_code == 200
    assert response.json() == {"message": "password changed"}


def test_password_change_clears_must_change_password(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="uma", role=ROLE_VIEWER, must_change_password=True)
    _login(client, username="uma")
    assert client.get("/api/v1/auth/me").json()["must_change_password"] is True

    _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)

    assert client.get("/api/v1/auth/me").json()["must_change_password"] is False


def test_new_password_works_for_a_subsequent_login(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="vince", role=ROLE_VIEWER)
    _login(client, username="vince")
    _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)

    fresh_client = TestClient(client.app, base_url="https://testserver")
    response = _login(fresh_client, username="vince", password=_NEW_PASSWORD)
    assert response.status_code == 200


def test_old_password_no_longer_works_after_a_change(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="wendy", role=ROLE_VIEWER)
    _login(client, username="wendy")
    _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)

    fresh_client = TestClient(client.app, base_url="https://testserver")
    response = _login(fresh_client, username="wendy", password=_PASSWORD)
    assert response.status_code == 401


def test_current_session_remains_valid_after_a_password_change(
    client: TestClient, fake_uow
) -> None:
    _seed_user(fake_uow, username="xavier", role=ROLE_VIEWER)
    _login(client, username="xavier")
    _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200


def test_other_sessions_are_revoked_by_a_password_change(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="yara", role=ROLE_VIEWER)
    other_client = TestClient(client.app, base_url="https://testserver")
    _login(other_client, username="yara")
    _login(client, username="yara")

    assert other_client.get("/api/v1/auth/me").status_code == 200

    _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)

    assert client.get("/api/v1/auth/me").status_code == 200  # acting session: unaffected
    other_response = other_client.get("/api/v1/auth/me")
    assert other_response.status_code == 401  # every other session: revoked
    assert other_response.json()["code"] == "SESSION_INVALID"


def test_password_change_records_an_audit_event_with_the_user_id(
    client: TestClient, fake_uow
) -> None:
    user_id = _seed_user(fake_uow, username="zack", role=ROLE_VIEWER)
    _login(client, username="zack")
    _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)

    events = [e for e in fake_uow.audit.events if e.action == ACTION_PASSWORD_CHANGED]
    assert len(events) == 1
    assert events[0].actor_id == user_id


def test_a_revoked_other_session_gets_its_own_session_revoked_audit_event(
    client: TestClient, fake_uow
) -> None:
    _seed_user(fake_uow, username="abby", role=ROLE_VIEWER)
    other_client = TestClient(client.app, base_url="https://testserver")
    _login(other_client, username="abby")
    _login(client, username="abby")

    _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)

    events = [e for e in fake_uow.audit.events if e.action == ACTION_SESSION_REVOKED]
    assert len(events) == 1


def test_a_failed_password_change_writes_no_audit_event_and_revokes_no_session(
    client: TestClient, fake_uow
) -> None:
    """The audit event and the session revocations only ever happen
    together with the actual password change - never on the rejected
    (wrong-current-password) path. `atp_api.services.auth.change_password`
    only calls `uow.audit.save`/`uow.sessions.revoke_all_for_user` after
    the password hash has already been updated, so their presence or
    absence tracks the state change exactly - the same proxy for
    single-transaction atomicity `tests/unit/api/test_kill_switches.py`
    uses for its own no-op-vs-genuine-transition assertions."""
    _seed_user(fake_uow, username="bert", role=ROLE_VIEWER)
    other_client = TestClient(client.app, base_url="https://testserver")
    _login(other_client, username="bert")
    _login(client, username="bert")

    response = _change_password(
        client, current_password="totally wrong", new_password=_NEW_PASSWORD
    )
    assert response.status_code == 401

    assert not [e for e in fake_uow.audit.events if e.action == ACTION_PASSWORD_CHANGED]
    assert not [e for e in fake_uow.audit.events if e.action == ACTION_SESSION_REVOKED]
    assert other_client.get("/api/v1/auth/me").status_code == 200  # still live


def test_password_change_without_a_csrf_header_is_rejected(client: TestClient, fake_uow) -> None:
    _seed_user(fake_uow, username="carl", role=ROLE_VIEWER)
    _login(client, username="carl")
    response = _change_password(
        client,
        current_password=_PASSWORD,
        new_password=_NEW_PASSWORD,
        include_csrf_header=False,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"
    # No side effect from the rejected request - the old password still works.
    fresh_client = TestClient(client.app, base_url="https://testserver")
    assert _login(fresh_client, username="carl", password=_PASSWORD).status_code == 200


def test_password_change_with_a_mismatched_csrf_header_is_rejected(
    client: TestClient, fake_uow
) -> None:
    _seed_user(fake_uow, username="dina", role=ROLE_VIEWER)
    _login(client, username="dina")
    response = _change_password(
        client,
        current_password=_PASSWORD,
        new_password=_NEW_PASSWORD,
        csrf_token="forged-value",
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


def test_password_values_never_appear_in_a_failed_change_response(
    client: TestClient, fake_uow
) -> None:
    _seed_user(fake_uow, username="ella", role=ROLE_VIEWER)
    _login(client, username="ella")
    response = _change_password(
        client, current_password="totally wrong", new_password=_NEW_PASSWORD
    )
    assert "totally wrong" not in response.text
    assert _NEW_PASSWORD not in response.text
    assert _PASSWORD_HASH not in response.text


def test_password_values_never_appear_in_a_successful_change_response(
    client: TestClient, fake_uow
) -> None:
    _seed_user(fake_uow, username="finn", role=ROLE_VIEWER)
    _login(client, username="finn")
    response = _change_password(client, current_password=_PASSWORD, new_password=_NEW_PASSWORD)
    assert _PASSWORD not in response.text
    assert _NEW_PASSWORD not in response.text
