"""`GET /api/v1/instruments` (Phase 1 Step 10) - exercised through real HTTP
requests against the fully-wired app with `get_instrument_repository`
swapped for an in-memory fake (`tests/unit/api/conftest.py`). No Docker
required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_PAPER_TRADER, ROLE_VIEWER
from atp_persistence.repositories import UserRecord
from tests.unit.api.fakes import FakeInstrumentRow, FakeUnitOfWork

_PASSWORD = "correct horse battery staple"
_PASSWORD_HASH = hash_password(_PASSWORD)  # module-level: argon2 hashing is slow, hash once


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


def _instrument(
    *, instrument_id: str = "instr-1", symbol: str = "FIXTURE1", active_to: datetime | None = None
) -> FakeInstrumentRow:
    return FakeInstrumentRow(
        instrument_id=instrument_id,
        symbol=symbol,
        name=f"{symbol} Limited",
        exchange="NSE",
        segment="EQ",
        lot_size=1,
        tick_size=Decimal("0.05"),
        active_to=active_to,
    )


def test_get_instruments_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/instruments")
    assert response.status_code == 401


def test_get_instruments_requires_read_instruments_permission(client: TestClient, fake_uow) -> None:
    # viewer only has READ_SYSTEM (rbac.py) - no READ_INSTRUMENTS.
    _seed_user(fake_uow, username="viewer1", role=ROLE_VIEWER)
    _login(client, username="viewer1")

    response = client.get("/api/v1/instruments")
    assert response.status_code == 403


def test_get_instruments_returns_active_instruments(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    fake_instrument_repository._by_id["instr-1"] = _instrument(instrument_id="instr-1")
    _seed_user(fake_uow, username="trader1", role=ROLE_PAPER_TRADER)
    _login(client, username="trader1")

    response = client.get("/api/v1/instruments")
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["instrument_id"] == "instr-1"
    assert item["symbol"] == "FIXTURE1"
    assert item["lot_size"] == 1
    assert item["tick_size"] == "0.05"


def test_get_instruments_excludes_inactive_rows(
    client: TestClient, fake_uow, fake_instrument_repository
) -> None:
    fake_instrument_repository._by_id["active"] = _instrument(
        instrument_id="active", symbol="ACTIVE"
    )
    fake_instrument_repository._by_id["retired"] = _instrument(
        instrument_id="retired",
        symbol="RETIRED",
        active_to=datetime(2025, 1, 1, tzinfo=UTC),
    )
    _seed_user(fake_uow, username="trader2", role=ROLE_PAPER_TRADER)
    _login(client, username="trader2")

    response = client.get("/api/v1/instruments")
    symbols = {item["symbol"] for item in response.json()["items"]}
    assert symbols == {"ACTIVE"}
