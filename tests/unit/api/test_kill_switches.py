"""`POST /api/v1/kill-switches/{switch_id}/engage`/`.../disengage`
(Phase 1 Step 14, ADR-007) - exercised through real HTTP requests against
the fully-wired app, database dependencies swapped for in-memory fakes
(`tests/unit/api/conftest.py`), mirroring `test_paper_proposals.py`'s
established precedent: genuine RBAC/CSRF/validation flows are otherwise
untestable without Docker.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from atp_api.security.cookies import CSRF_COOKIE_NAME
from atp_api.security.passwords import hash_password
from atp_api.security.rbac import (
    ROLE_ADMINISTRATOR,
    ROLE_LIVE_TRADER,
    ROLE_PAPER_TRADER,
    ROLE_RESEARCHER,
    ROLE_VIEWER,
)
from atp_domain.audit import ACTION_KILL_SWITCH_DISENGAGED, ACTION_KILL_SWITCH_ENGAGED
from atp_persistence.repositories import KillSwitchStateSnapshot, UserRecord
from tests.unit.api.fakes import FakeUnitOfWork

_PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hash_password(_PASSWORD)  # module-level: argon2 hashing is slow, hash once
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_user(uow: FakeUnitOfWork, *, username: str, role: str) -> str:
    created_at = _NOW
    user_id = f"user-{username}"
    uow.users._by_id[user_id] = UserRecord(
        user_id=user_id,
        username=username,
        password_hash=_PASSWORD_HASH,
        role=role,
        is_active=True,
        must_change_password=False,
        created_at=created_at,
        updated_at=created_at,
    )
    return user_id


def _login(client: TestClient, *, username: str):
    return client.post("/api/v1/auth/login", json={"username": username, "password": _PASSWORD})


def _authenticated(
    client: TestClient, fake_uow: FakeUnitOfWork, *, role: str, username: str = "u1"
):
    _seed_user(fake_uow, username=username, role=role)
    _login(client, username=username)


def _mutate(
    client: TestClient,
    switch_id: str,
    verb: str,
    *,
    reason: str | None = "operator action",
    with_csrf: bool = True,
):
    headers = {}
    if with_csrf:
        csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
        headers["x-csrf-token"] = csrf_cookie
    body = {} if reason is None else {"reason": reason}
    return client.post(f"/api/v1/kill-switches/{switch_id}/{verb}", json=body, headers=headers)


def _kill_switch_audit_events(fake_uow: FakeUnitOfWork) -> list:
    """`_login` itself writes an `ACTION_LOGIN_SUCCEEDED` audit event
    before any test action runs - every assertion about *this* module's
    audit writes filters to just the two actions it owns, rather than
    asserting on `fake_uow.audit.events` directly, which would otherwise
    always contain one extra, unrelated event."""
    return [
        event
        for event in fake_uow.audit.events
        if event.action in (ACTION_KILL_SWITCH_ENGAGED, ACTION_KILL_SWITCH_DISENGAGED)
    ]


def _seed_switch(
    fake_uow: FakeUnitOfWork, *, switch_id: str, engaged: bool, updated_by: str | None = None
) -> None:
    fake_uow.kill_switches._states[switch_id] = KillSwitchStateSnapshot(
        switch_id=switch_id, engaged=engaged, updated_at=_NOW, updated_by=updated_by, reason=None
    )


# --- engage/disengage RBAC asymmetry (ADR-007) ---------------------------


def test_paper_trader_can_engage_paper_switch(client: TestClient, fake_uow: FakeUnitOfWork) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=False)
    _authenticated(client, fake_uow, role=ROLE_PAPER_TRADER)

    response = _mutate(client, "PAPER", "engage")

    assert response.status_code == 200
    assert response.json()["engaged"] is True


def test_paper_trader_cannot_disengage(client: TestClient, fake_uow: FakeUnitOfWork) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=True)
    _authenticated(client, fake_uow, role=ROLE_PAPER_TRADER)

    response = _mutate(client, "PAPER", "disengage")

    assert response.status_code == 403
    assert fake_uow.kill_switches.apply_transition_calls == []


@pytest.mark.parametrize("role", [ROLE_PAPER_TRADER, ROLE_LIVE_TRADER, ROLE_ADMINISTRATOR])
def test_engage_is_permitted_for_every_trading_capable_role(
    client: TestClient, fake_uow: FakeUnitOfWork, role: str
) -> None:
    _seed_switch(fake_uow, switch_id="API_EXECUTION", engaged=False)
    _authenticated(client, fake_uow, role=role)

    response = _mutate(client, "API_EXECUTION", "engage")

    assert response.status_code == 200


@pytest.mark.parametrize("role", [ROLE_VIEWER, ROLE_RESEARCHER])
def test_engage_is_forbidden_below_paper_trader(
    client: TestClient, fake_uow: FakeUnitOfWork, role: str
) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=False)
    _authenticated(client, fake_uow, role=role)

    response = _mutate(client, "PAPER", "engage")

    assert response.status_code == 403
    assert fake_uow.kill_switches.apply_transition_calls == []


def test_administrator_can_engage_and_disengage(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=False)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    engage_response = _mutate(client, "PAPER", "engage")
    assert engage_response.status_code == 200
    assert engage_response.json()["engaged"] is True

    disengage_response = _mutate(client, "PAPER", "disengage")
    assert disengage_response.status_code == 200
    assert disengage_response.json()["engaged"] is False


# --- GLOBAL_LIVE / LIVE_ACCOUNT have no mutation route in Phase 1 --------


@pytest.mark.parametrize("switch_id", ["GLOBAL_LIVE", "LIVE_ACCOUNT"])
@pytest.mark.parametrize("verb", ["engage", "disengage"])
def test_global_live_and_live_account_reject_mutation_even_for_administrator(
    client: TestClient, fake_uow: FakeUnitOfWork, switch_id: str, verb: str
) -> None:
    """The strongest role in the system, on either verb, against either
    permanently-unclearable switch - still rejected. ADR-007: 'No - no
    route exists to clear it.'"""
    _seed_switch(fake_uow, switch_id=switch_id, engaged=True)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    response = _mutate(client, switch_id, verb)

    assert response.status_code == 403
    assert fake_uow.kill_switches.apply_transition_calls == []
    assert _kill_switch_audit_events(fake_uow) == []


# --- validation -----------------------------------------------------------


def test_unknown_switch_scope_is_rejected_with_422(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    response = _mutate(client, "NOT_A_REAL_SCOPE", "engage")

    assert response.status_code == 422
    assert fake_uow.kill_switches.apply_transition_calls == []


def test_missing_reason_is_rejected_with_422(client: TestClient, fake_uow: FakeUnitOfWork) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=False)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    response = _mutate(client, "PAPER", "engage", reason=None)

    assert response.status_code == 422
    assert fake_uow.kill_switches.apply_transition_calls == []


def test_empty_reason_is_rejected_with_422(client: TestClient, fake_uow: FakeUnitOfWork) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=False)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    response = _mutate(client, "PAPER", "engage", reason="")

    assert response.status_code == 422


def test_csrf_is_enforced_on_engage(client: TestClient, fake_uow: FakeUnitOfWork) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=False)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    response = _mutate(client, "PAPER", "engage", with_csrf=False)

    assert response.status_code == 403
    assert fake_uow.kill_switches.apply_transition_calls == []


# --- idempotency, atomicity, history (Phase 1 Step 14) -------------------


def test_engaging_an_already_engaged_switch_is_a_no_op(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=True)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    response = _mutate(client, "PAPER", "engage")

    assert response.status_code == 200
    assert response.json()["engaged"] is True
    assert fake_uow.kill_switches.apply_transition_calls == []
    assert fake_uow.kill_switches.history == []
    assert _kill_switch_audit_events(fake_uow) == []


def test_a_genuine_transition_appends_exactly_one_history_entry(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=False)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    _mutate(client, "PAPER", "engage", reason="incident response")

    assert len(fake_uow.kill_switches.history) == 1
    entry = fake_uow.kill_switches.history[0]
    assert entry.switch_id == "PAPER"
    assert entry.previous_engaged is False
    assert entry.new_engaged is True
    assert entry.reason == "incident response"


def test_a_genuine_transition_writes_exactly_one_audit_event_atomically_with_the_state_change(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    """Both the fake `apply_transition` call and the fake `audit.save`
    call happen inside the same request - proving the service orchestrates
    them together, not that any real transaction exists (that needs
    Docker; see `tests/integration/db/`)."""
    _seed_switch(fake_uow, switch_id="PAPER", engaged=False)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    _mutate(client, "PAPER", "engage")

    kill_switch_events = _kill_switch_audit_events(fake_uow)
    assert len(kill_switch_events) == 1
    assert kill_switch_events[0].action == ACTION_KILL_SWITCH_ENGAGED
    assert len(fake_uow.kill_switches.history) == 1
    # The same minted event_id links the two records, exactly like
    # `core.kill_switch_history.audit_event_id` is documented to.
    assert fake_uow.kill_switches.history[0].audit_event_id == kill_switch_events[0].event_id


def test_disengage_writes_the_disengaged_audit_action(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _seed_switch(fake_uow, switch_id="PAPER", engaged=True)
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    _mutate(client, "PAPER", "disengage")

    assert fake_uow.audit.events[-1].action == ACTION_KILL_SWITCH_DISENGAGED


def test_first_transition_of_a_strategy_switch_creates_it(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    """`STRATEGY:{id}`/`INSTRUMENT:{id}` rows are created on demand
    (`docs/schemas/kill_switch_state.md`) - no seed needed."""
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    response = _mutate(client, "STRATEGY:momentum-v1", "engage")

    assert response.status_code == 200
    assert response.json()["switch_id"] == "STRATEGY:momentum-v1"
    assert response.json()["engaged"] is True
    assert fake_uow.kill_switches.history[0].previous_engaged is False


def test_audit_event_carries_the_strategy_id_for_a_strategy_scoped_switch(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    _mutate(client, "STRATEGY:momentum-v1", "engage")

    event = _kill_switch_audit_events(fake_uow)[0]
    assert event.strategy_id == "momentum-v1"
    assert event.instrument_id is None


def test_audit_event_carries_the_instrument_id_for_an_instrument_scoped_switch(
    client: TestClient, fake_uow: FakeUnitOfWork
) -> None:
    _authenticated(client, fake_uow, role=ROLE_ADMINISTRATOR)

    _mutate(client, "INSTRUMENT:NSE:INFY", "engage")

    event = _kill_switch_audit_events(fake_uow)[0]
    assert event.instrument_id == "NSE:INFY"
    assert event.strategy_id is None
