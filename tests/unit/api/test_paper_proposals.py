"""`POST /api/v1/paper/proposals` (Phase 1 Step 10, ADR-012) - exercised
through real HTTP requests against the fully-wired app, database
dependencies swapped for in-memory fakes (`tests/unit/api/conftest.py`).
No Docker required - genuine idempotent-replay/conflict/CSRF/RBAC flows
are otherwise untestable without it, mirroring `test_auth_flows.py`'s
established precedent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

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
from atp_domain.audit import ACTION_PROPOSAL_CREATED
from atp_persistence.repositories import UserRecord
from tests.unit.api.fakes import FakeInstrumentRow, FakeUnitOfWork

_PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hash_password(_PASSWORD)  # module-level: argon2 hashing is slow, hash once
_INSTRUMENT_ID = "instr-1"


def _seed_user(uow: FakeUnitOfWork, *, username: str, role: str) -> str:
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
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


def _seed_instrument(repository, *, instrument_id: str = _INSTRUMENT_ID) -> None:
    repository._by_id[instrument_id] = FakeInstrumentRow(
        instrument_id=instrument_id,
        symbol="FIXTURE1",
        name="Fixture One Limited",
        exchange="NSE",
        segment="EQ",
        lot_size=1,
        tick_size=Decimal("0.05"),
    )


def _limit_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "instrument_id": _INSTRUMENT_ID,
        "side": "BUY",
        "quantity": "10",
        "order_type": "LIMIT",
        "limit_price": "100.50",
        "product": "CNC",
        "client_request_id": "req-1",
    }
    payload.update(overrides)
    return payload


def _post_proposal(client: TestClient, payload: dict[str, object], *, with_csrf: bool = True):
    headers = {}
    if with_csrf:
        csrf_cookie = client.cookies.get(CSRF_COOKIE_NAME)
        headers["x-csrf-token"] = csrf_cookie
    return client.post("/api/v1/paper/proposals", json=payload, headers=headers)


def _authenticated_trader(client: TestClient, fake_uow: FakeUnitOfWork, *, username: str = "t1"):
    _seed_user(fake_uow, username=username, role=ROLE_PAPER_TRADER)
    _login(client, username=username)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_valid_limit_proposal_is_accepted_and_persisted_with_server_set_fields(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    response = _post_proposal(client, _limit_payload())

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING_EVALUATION"
    assert body["client_request_id"] == "req-1"
    assert body.get("proposal_id")

    saved = fake_uow.trade_proposals._by_id[body["proposal_id"]]
    assert saved.mode.value == "PAPER"
    assert saved.created_at.tzinfo is not None
    assert str(saved.proposal_id) == body["proposal_id"]


def test_market_proposal_is_accepted_and_not_pre_judged(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    """Intake must not special-case MARKET orders - RISK.DATA.001's
    deterministic rejection happens later, inside atp_exec_paper (ADR-012
    point on out-of-scope MARKET special-casing)."""
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    payload = _limit_payload(order_type="MARKET", limit_price=None, client_request_id="req-market")
    response = _post_proposal(client, payload)

    assert response.status_code == 202
    assert response.json()["status"] == "PENDING_EVALUATION"


def test_audit_event_is_written_via_the_same_uow_as_the_proposal(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    """`paper_proposals.submit_proposal` writes the proposal and its audit
    event through the same `uow` before returning (`paper_proposals.py`) -
    both share one real PostgreSQL transaction in production
    (`atp_persistence.db.unit_of_work` commits once, at the very end of the
    request). This fake harness's `get_unit_of_work` override
    (`tests/unit/api/conftest.py`) never calls `.commit()` at all, so
    `fake_uow.committed` cannot itself prove atomicity here - the genuine
    same-transaction proof against a real database lives in
    `tests/integration/db/test_paper_proposal_intake.py`, Docker-gated."""
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    response = _post_proposal(client, _limit_payload())
    assert response.status_code == 202
    assert fake_uow.rolled_back is False
    assert any(event.action == ACTION_PROPOSAL_CREATED for event in fake_uow.audit.events)


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def test_limit_price_on_a_market_order_is_rejected(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    payload = _limit_payload(order_type="MARKET", limit_price="100")
    response = _post_proposal(client, payload)
    assert response.status_code == 422


@pytest.mark.parametrize("quantity", ["0", "-5"])
def test_nonpositive_quantity_is_rejected(
    client: TestClient, fake_uow, fake_instrument_repository, quantity: str
) -> None:
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    response = _post_proposal(client, _limit_payload(quantity=quantity))
    assert response.status_code == 422


def test_missing_quantity_is_rejected(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    payload = _limit_payload()
    del payload["quantity"]
    response = _post_proposal(client, payload)
    assert response.status_code == 422


def test_unknown_instrument_id_is_rejected(client: TestClient, fake_uow) -> None:
    # Deliberately not seeded into fake_instrument_repository.
    _authenticated_trader(client, fake_uow)

    response = _post_proposal(client, _limit_payload(instrument_id="does-not-exist"))
    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_INSTRUMENT"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mode", "PAPER"),
        ("created_by", "someone-else"),
        ("proposal_id", "00000000-0000-7000-8000-000000000099"),
        ("created_at", "2020-01-01T00:00:00Z"),
    ],
)
def test_caller_supplied_server_set_field_is_rejected(
    client: TestClient, fake_uow, fake_instrument_repository, field: str, value: str
) -> None:
    """`mode`/`created_by`/`proposal_id`/`created_at` are always server-set
    (ADR-012 point 4) - `ApiModel`'s `extra="forbid"` rejects the extra
    field outright, before any service code runs."""
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    response = _post_proposal(client, _limit_payload(**{field: value}))
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_duplicate_client_request_id_with_identical_payload_replays(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    first = _post_proposal(client, _limit_payload())
    second = _post_proposal(client, _limit_payload())

    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["proposal_id"] == second.json()["proposal_id"]
    assert len(fake_uow.trade_proposals._by_id) == 1


def test_duplicate_client_request_id_with_different_payload_conflicts(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    first = _post_proposal(client, _limit_payload())
    second = _post_proposal(client, _limit_payload(quantity="20"))

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["code"] == "PROPOSAL_CONFLICT"
    assert len(fake_uow.trade_proposals._by_id) == 1


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------


def test_submit_without_csrf_header_is_rejected(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    response = _post_proposal(client, _limit_payload(), with_csrf=False)
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


def test_submit_with_mismatched_csrf_header_is_rejected(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    _seed_instrument(fake_instrument_repository)
    _authenticated_trader(client, fake_uow)

    response = client.post(
        "/api/v1/paper/proposals",
        json=_limit_payload(),
        headers={"x-csrf-token": "forged-value"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


# ---------------------------------------------------------------------------
# RBAC matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "expect_allowed"),
    [
        (ROLE_VIEWER, False),
        (ROLE_RESEARCHER, False),
        (ROLE_PAPER_TRADER, True),
        (ROLE_LIVE_TRADER, True),
        (ROLE_ADMINISTRATOR, True),
    ],
)
def test_submit_proposal_permission_matrix(
    client: TestClient,
    fake_uow,
    fake_instrument_repository,
    role: str,
    expect_allowed: bool,
) -> None:
    _seed_instrument(fake_instrument_repository)
    _seed_user(fake_uow, username="matrix-user", role=role)
    _login(client, username="matrix-user")

    response = _post_proposal(client, _limit_payload(client_request_id=f"req-{role}"))
    if expect_allowed:
        assert response.status_code == 202
    else:
        assert response.status_code == 403


def test_submit_proposal_requires_authentication(client: TestClient) -> None:
    response = client.post("/api/v1/paper/proposals", json=_limit_payload())
    assert response.status_code == 401
