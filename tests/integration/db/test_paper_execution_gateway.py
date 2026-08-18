"""Phase 1 Step 9 (ADR-011): the paper execution gateway against a real,
migrated database, using the actual `atp_paper_exec` role.

`tests/unit/exec_paper/test_gateway.py` already exercises the gateway's
logic extensively against in-memory fakes; this file's job is narrower:
prove the real repositories round-trip through actual PostgreSQL tables and
grants (including that `atp_paper_exec`'s `SELECT`-only privilege on
`paper.trade_proposals` is sufficient - the whole point of ADR-011's
unlocked-SELECT design), and prove the `UNIQUE (proposal_id)` constraint on
`paper.risk_decisions` genuinely arbitrates a concurrent double-claim -
something no in-memory fake can prove on its own.
"""

from __future__ import annotations

import asyncio
import uuid

import psycopg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_exec_paper.gateway import run_once
from atp_persistence.db import make_session_factory


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_async_psycopg_url(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


@pytest.fixture
def seeded_instrument_id(migrated_database: str, owner_connection: psycopg.Connection) -> str:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT instrument_id FROM core.instruments WHERE provider = 'FIXTURE' LIMIT 1")
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None
    return row[0]


@pytest.fixture
def seeded_user_id(migrated_database: str, owner_connection: psycopg.Connection):
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
    with owner_connection.cursor() as cur:
        cur.execute("DELETE FROM core.users WHERE user_id = %s", (user_id,))
    owner_connection.commit()


def _insert_proposal(
    owner_connection: psycopg.Connection,
    *,
    instrument_id: str,
    user_id: str,
    order_type: str = "LIMIT",
    limit_price: str | None = "100",
    side: str = "BUY",
    quantity: str = "10",
) -> str:
    proposal_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper.trade_proposals
                (proposal_id, mode, instrument_id, side, quantity, order_type, limit_price,
                 trigger_price, product, client_request_id, expected_risk, created_by, created_at)
            VALUES
                (%s, 'PAPER', %s, %s, %s, %s, %s, NULL, 'CNC', %s, '{}', %s, now())
            """,
            (
                proposal_id,
                instrument_id,
                side,
                quantity,
                order_type,
                limit_price,
                f"req-{proposal_id}",
                user_id,
            ),
        )
    owner_connection.commit()
    return proposal_id


def test_approved_limit_proposal_executes_end_to_end_via_paper_exec_role(
    owner_dsn: str,
    paper_exec_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_user_id: str,
) -> None:
    proposal_id = _insert_proposal(
        owner_connection, instrument_id=seeded_instrument_id, user_id=seeded_user_id
    )

    async def run() -> None:
        engine = create_async_engine(_as_async_psycopg_url(paper_exec_dsn))
        try:
            session_factory = make_session_factory(engine)
            outcome = await run_once(
                session_factory,
                proposal_id,
                id_generator=UUIDv7Generator(),
                clock=UTCClock(),
            )
            assert outcome.already_claimed is False
            assert outcome.order_id is not None
        finally:
            await engine.dispose()

    asyncio.run(run())

    with owner_connection.cursor() as cur:
        cur.execute("SELECT status FROM paper.orders WHERE proposal_id = %s", (proposal_id,))
        order_row = cur.fetchone()
        cur.execute(
            "SELECT simulated, source FROM paper.fills f "
            "JOIN paper.orders o ON o.internal_order_id = f.internal_order_id "
            "WHERE o.proposal_id = %s",
            (proposal_id,),
        )
        fill_row = cur.fetchone()
    owner_connection.rollback()

    assert order_row is not None
    assert order_row[0] == "FILLED"
    assert fill_row is not None
    assert fill_row[0] is True
    assert fill_row[1] == "PAPER_SIMULATOR"


def test_market_proposal_is_rejected_with_no_fill(
    owner_dsn: str,
    paper_exec_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_user_id: str,
) -> None:
    proposal_id = _insert_proposal(
        owner_connection,
        instrument_id=seeded_instrument_id,
        user_id=seeded_user_id,
        order_type="MARKET",
        limit_price=None,
    )

    async def run() -> None:
        engine = create_async_engine(_as_async_psycopg_url(paper_exec_dsn))
        try:
            session_factory = make_session_factory(engine)
            outcome = await run_once(
                session_factory,
                proposal_id,
                id_generator=UUIDv7Generator(),
                clock=UTCClock(),
            )
            assert outcome.decision_outcome is not None
            assert outcome.decision_outcome.value == "REJECTED"
            assert outcome.order_id is None
        finally:
            await engine.dispose()

    asyncio.run(run())

    with owner_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM paper.orders WHERE proposal_id = %s", (proposal_id,))
        order_count = cur.fetchone()[0]
    owner_connection.rollback()
    assert order_count == 0


def test_concurrent_execution_of_the_same_proposal_creates_exactly_one_order(
    owner_dsn: str,
    paper_exec_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_user_id: str,
) -> None:
    """The real proof of ADR-011: two genuinely concurrent `run_once` calls
    against the same `proposal_id`, over two separate engines/connections -
    the database's own `UNIQUE (proposal_id)` constraint on
    `paper.risk_decisions` is what makes exactly one of them win."""
    proposal_id = _insert_proposal(
        owner_connection, instrument_id=seeded_instrument_id, user_id=seeded_user_id
    )

    async def run() -> None:
        engine_a = create_async_engine(_as_async_psycopg_url(paper_exec_dsn))
        engine_b = create_async_engine(_as_async_psycopg_url(paper_exec_dsn))
        try:
            results = await asyncio.gather(
                run_once(
                    make_session_factory(engine_a),
                    proposal_id,
                    id_generator=UUIDv7Generator(),
                    clock=UTCClock(),
                ),
                run_once(
                    make_session_factory(engine_b),
                    proposal_id,
                    id_generator=UUIDv7Generator(),
                    clock=UTCClock(),
                ),
            )
        finally:
            await engine_a.dispose()
            await engine_b.dispose()
        already_claimed_flags = [r.already_claimed for r in results]
        assert already_claimed_flags.count(True) == 1
        assert already_claimed_flags.count(False) == 1

    asyncio.run(run())

    with owner_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM paper.orders WHERE proposal_id = %s", (proposal_id,))
        order_count = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM paper.risk_decisions WHERE proposal_id = %s", (proposal_id,)
        )
        decision_count = cur.fetchone()[0]
    owner_connection.rollback()
    assert order_count == 1
    assert decision_count == 1
