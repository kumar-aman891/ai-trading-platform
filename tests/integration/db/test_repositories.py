"""Phase 1 Step 6: the three concrete repositories
(`atp_persistence.repositories`) against a real database - session/Unit of
Work boundaries, transaction commit/rollback, and idempotency-key
uniqueness enforced through the repository layer, not just raw SQL.

Repository protocols declare `async def`, so these tests drive them with
`asyncio.run()` inside ordinary sync test functions rather than pulling in
a pytest-asyncio dependency for three test functions.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
import sqlalchemy.exc
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from atp_domain.money import Quantity
from atp_domain.orders import Order
from atp_domain.proposals import TradeProposal
from atp_domain.types import (
    IntentId,
    Mode,
    OrderId,
    OrderStatus,
    OrderType,
    Product,
    ProposalId,
    Side,
    StrategyId,
)
from atp_persistence.db import create_engine, make_session_factory, unit_of_work
from tests.integration.db.conftest import delete_user_cascade


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_sync_psycopg_url(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


def _as_async_psycopg_url(dsn: str) -> str:
    return _as_sync_psycopg_url(dsn)


@pytest.fixture
def seeded_instrument_id(migrated_database: str, owner_connection: psycopg.Connection) -> str:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT instrument_id FROM core.instruments WHERE provider = 'FIXTURE' LIMIT 1")
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None
    # `str(...)`: psycopg3 maps a Postgres `uuid` column to a Python
    # `UUID` object, but every domain identifier is `NewType("...", str)`
    # and `uuid_pk` uses `as_uuid=False`, so SQLAlchemy hands the app a
    # plain str. Returning the raw UUID made round-trip assertions compare
    # UUID('...') against '...' and fail (Phase 1 Step 12 Phase A).
    return str(row[0])


@pytest.fixture
def seeded_user_id(migrated_database: str, owner_connection: psycopg.Connection) -> Iterator[str]:
    user_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.users (user_id, username, password_hash, role, created_at, updated_at)
            VALUES (%s, %s, 'argon2id$fixture', 'administrator', now(), now())
            """,
            (user_id, f"fixture-{user_id}"),
        )
    owner_connection.commit()
    yield user_id
    delete_user_cascade(owner_connection, user_id)


def _make_proposal(instrument_id: str, *, client_request_id: str) -> TradeProposal:
    return TradeProposal(
        proposal_id=ProposalId(_new_uuid()),
        mode=Mode.PAPER,
        instrument_id=instrument_id,  # type: ignore[arg-type]
        side=Side.BUY,
        quantity=Quantity(Decimal("1")),
        order_type=OrderType.MARKET,
        limit_price=None,
        trigger_price=None,
        product=Product.CNC,
        client_request_id=client_request_id,
        created_at=datetime.now(UTC),
    )


def test_trade_proposal_repository_save_and_get_round_trips(
    owner_dsn: str, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    async def run() -> None:
        engine = create_engine(_as_async_psycopg_url(owner_dsn))
        try:
            session_factory = make_session_factory(engine)
            proposal = _make_proposal(seeded_instrument_id, client_request_id=_new_uuid())
            async with unit_of_work(session_factory) as uow:
                await uow.trade_proposals.save(proposal, created_by=seeded_user_id)

            async with unit_of_work(session_factory) as uow:
                fetched = await uow.trade_proposals.get(proposal.proposal_id)
            assert fetched == proposal
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_trade_proposal_repository_save_accepts_none_created_by_with_strategy_id(
    owner_dsn: str, owner_connection: psycopg.Connection, seeded_instrument_id: str
) -> None:
    """ADR-015: a strategy-authored proposal - created_by=None,
    strategy_id set - round-trips through the real repository/database
    with no core.users row involved.

    The row is deleted explicitly rather than left for `delete_user_cascade`:
    that helper is keyed on `created_by`, so once ADR-015 made the column
    nullable it became structurally unable to reach a strategy-authored
    row. A leaked `created_by IS NULL` row is not harmless - it makes every
    later `alembic downgrade` past 0006 fail at
    `ALTER COLUMN created_by SET NOT NULL` (found by CI, Milestone 2D)."""
    proposal_id = _new_uuid()

    async def run() -> None:
        engine = create_engine(_as_async_psycopg_url(owner_dsn))
        try:
            session_factory = make_session_factory(engine)
            proposal = TradeProposal(
                proposal_id=ProposalId(proposal_id),
                mode=Mode.PAPER,
                instrument_id=seeded_instrument_id,  # type: ignore[arg-type]
                side=Side.BUY,
                quantity=Quantity(Decimal("1")),
                order_type=OrderType.MARKET,
                limit_price=None,
                trigger_price=None,
                product=Product.CNC,
                client_request_id=_new_uuid(),
                created_at=datetime.now(UTC),
                strategy_id=StrategyId(_new_uuid()),
                strategy_version=1,
            )
            async with unit_of_work(session_factory) as uow:
                await uow.trade_proposals.save(proposal, created_by=None)

            async with unit_of_work(session_factory) as uow:
                fetched = await uow.trade_proposals.get(proposal.proposal_id)
            assert fetched == proposal
        finally:
            await engine.dispose()

    try:
        asyncio.run(run())
    finally:
        with owner_connection.cursor() as cur:
            cur.execute("DELETE FROM paper.trade_proposals WHERE proposal_id = %s", (proposal_id,))
        owner_connection.commit()


def test_trade_proposal_repository_get_returns_none_for_unknown_id(owner_dsn: str) -> None:
    async def run() -> None:
        engine = create_engine(_as_async_psycopg_url(owner_dsn))
        try:
            session_factory = make_session_factory(engine)
            async with unit_of_work(session_factory) as uow:
                fetched = await uow.trade_proposals.get(ProposalId(_new_uuid()))
            assert fetched is None
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_unit_of_work_rolls_back_on_exception_leaving_no_partial_state(
    owner_dsn: str, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    """A failure partway through a Unit of Work must not partially persist
    state (CLAUDE.md: keep execution idempotent; the Step 6 spec's
    "TRANSACTIONS: failed transaction does not partially persist state")."""

    async def run() -> None:
        engine = create_engine(_as_async_psycopg_url(owner_dsn))
        try:
            session_factory = make_session_factory(engine)
            proposal = _make_proposal(seeded_instrument_id, client_request_id=_new_uuid())

            class _Boom(Exception):
                pass

            with pytest.raises(_Boom):
                async with unit_of_work(session_factory) as uow:
                    await uow.trade_proposals.save(proposal, created_by=seeded_user_id)
                    raise _Boom("simulated failure after the write, before commit")

            async with unit_of_work(session_factory) as uow:
                fetched = await uow.trade_proposals.get(proposal.proposal_id)
            assert fetched is None, "a rolled-back Unit of Work must leave no trace"
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_order_repository_rejects_duplicate_idempotency_key(
    owner_dsn: str, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    """order.md: `UNIQUE (idempotency_key)` is "a database guarantee, not
    an application-logic check that could race." Proven here through the
    repository/session layer, not raw SQL."""

    async def run() -> None:
        engine = create_engine(_as_async_psycopg_url(owner_dsn))
        try:
            session_factory = make_session_factory(engine)
            idempotency_key = hashlib.sha256(_new_uuid().encode()).hexdigest()

            proposal_a = _make_proposal(seeded_instrument_id, client_request_id=_new_uuid())
            proposal_b = _make_proposal(seeded_instrument_id, client_request_id=_new_uuid())
            now = datetime.now(UTC)

            async with unit_of_work(session_factory) as uow:
                await uow.trade_proposals.save(proposal_a, created_by=seeded_user_id)
                await uow.trade_proposals.save(proposal_b, created_by=seeded_user_id)

            # Orders reference paper.order_intents(intent_id) - minting one
            # via raw SQL here (not through atp_domain.intents, which is
            # process-capability-gated) purely to satisfy the FK for this
            # persistence-layer test.
            intent_a, intent_b = _new_uuid(), _new_uuid()
            for proposal, intent_id in ((proposal_a, intent_a), (proposal_b, intent_b)):
                await _mint_fixture_intent(engine, proposal.proposal_id, seeded_user_id, intent_id)

            order_a = Order(
                internal_order_id=OrderId(_new_uuid()),
                mode=Mode.PAPER,
                proposal_id=proposal_a.proposal_id,
                intent_id=IntentId(intent_a),
                idempotency_key=idempotency_key,
                status=OrderStatus.SUBMITTED,
                submitted_at=now,
                acknowledged_at=now,
                last_update_at=now,
            )
            order_b = Order(
                internal_order_id=OrderId(_new_uuid()),
                mode=Mode.PAPER,
                proposal_id=proposal_b.proposal_id,
                intent_id=IntentId(intent_b),
                idempotency_key=idempotency_key,
                status=OrderStatus.SUBMITTED,
                submitted_at=now,
                acknowledged_at=now,
                last_update_at=now,
            )

            async with unit_of_work(session_factory) as uow:
                await uow.orders.save(order_a)

            with pytest.raises(sqlalchemy.exc.IntegrityError):
                async with unit_of_work(session_factory) as uow:
                    await uow.orders.save(order_b)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_order_repository_save_and_get_preserves_intent_id(
    owner_dsn: str, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    """Step 6 reconciliation Fix 2: `atp_domain.orders.Order.intent_id` -
    the ApprovedOrderIntent -> Order link (ADR-008) - round-trips through
    the repository layer against a real database, not just the in-memory
    mapper test."""

    async def run() -> None:
        engine = create_engine(_as_async_psycopg_url(owner_dsn))
        try:
            session_factory = make_session_factory(engine)
            proposal = _make_proposal(seeded_instrument_id, client_request_id=_new_uuid())

            async with unit_of_work(session_factory) as uow:
                await uow.trade_proposals.save(proposal, created_by=seeded_user_id)

            intent_id = _new_uuid()
            await _mint_fixture_intent(engine, proposal.proposal_id, seeded_user_id, intent_id)

            now = datetime.now(UTC)
            order = Order(
                internal_order_id=OrderId(_new_uuid()),
                mode=Mode.PAPER,
                proposal_id=proposal.proposal_id,
                intent_id=IntentId(intent_id),
                idempotency_key=hashlib.sha256(_new_uuid().encode()).hexdigest(),
                status=OrderStatus.SUBMITTED,
                submitted_at=now,
                acknowledged_at=now,
                last_update_at=now,
            )

            async with unit_of_work(session_factory) as uow:
                await uow.orders.save(order)

            async with unit_of_work(session_factory) as uow:
                fetched = await uow.orders.get(order.internal_order_id)
            assert fetched == order
            assert fetched is not None
            assert fetched.intent_id == intent_id
        finally:
            await engine.dispose()

    asyncio.run(run())


async def _mint_fixture_intent(
    engine: AsyncEngine, proposal_id: str, user_id: str, intent_id: str
) -> None:
    """Test-only helper: inserts a risk_decision + order_intent pair via
    raw SQL so an Order under test has a valid `intent_id` FK target,
    without going through the capability-gated `atp_domain.intents`
    minting path (which this persistence-layer test is not exercising)."""
    risk_config_id = _new_uuid()
    decision_id = _new_uuid()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO core.risk_config "
                "(risk_config_id, mode, version, config, config_hash, active, created_at, created_by) "
                "VALUES (:id, 'PAPER', :version, '{}', :hash, false, now(), :user_id)"
            ),
            {
                "id": risk_config_id,
                # Three bytes, not four: `version` is a Postgres `integer`
                # (max 2147483647) and four random bytes reach 4294967295,
                # overflowing on roughly half of all draws (Phase 1 Step 12
                # Phase A).
                "version": int.from_bytes(uuid.uuid4().bytes[:3], "big"),
                "hash": _new_uuid(),
                "user_id": user_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO paper.risk_decisions "
                "(decision_id, mode, proposal_id, outcome, rule_results, risk_config_id, "
                " limit_snapshot_hash, decided_at) "
                "VALUES (:decision_id, 'PAPER', :proposal_id, 'APPROVED', "
                ' \'[{"rule_id":"X","outcome":"PASS","message":"ok","evidence":{}}]\', '
                " :risk_config_id, 'hash', now())"
            ),
            {
                "decision_id": decision_id,
                "proposal_id": proposal_id,
                "risk_config_id": risk_config_id,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO paper.order_intents "
                "(intent_id, mode, decision_id, proposal_id, canonical_payload, payload_hash, "
                " minted_at, expires_at) "
                "VALUES (:intent_id, 'PAPER', :decision_id, :proposal_id, '{}', 'hash', now(), "
                " now() + interval '30 seconds')"
            ),
            {"intent_id": intent_id, "decision_id": decision_id, "proposal_id": proposal_id},
        )
