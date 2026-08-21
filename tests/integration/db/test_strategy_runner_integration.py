"""Strategy Framework Milestone 2D: the real `atp_strategy` runner against
a real, migrated PostgreSQL database, using the actual `atp_strategy`
role - and the real, unmodified `atp_exec_paper` claim loop downstream,
using the actual `atp_paper_exec` role.

Role/grant matrix and the `proposal_has_an_author` CHECK are already
proven in `test_strategy_role_grants.py` (Milestone 2B) and
`test_table_constraints.py` (Milestone 2A); this file's job is narrower
and is what no in-memory fake (`tests/unit/strategy/`) or fake-repository
runner test can prove:

- that `atp_strategy.runner.run_once`, `atp_strategy.context
  .build_strategy_context`, and `atp_strategy.proposals
  .persist_proposed_trade` genuinely round-trip through real tables under
  the real `atp_strategy` role's grants (`SELECT` on `core.instruments`/
  `core.kill_switch_state`, `INSERT`-only on `paper.trade_proposals`/
  `audit.audit_events`) - not merely that a fake with the same method
  names would;
- that a real, unmodified `STRATEGY:{key}` kill-switch row (or its
  absence) genuinely gates evaluation, the same fail-closed way
  `tests/unit/strategy/test_runner.py` already proves against fakes;
- that `UNIQUE (client_request_id)` genuinely arbitrates a concurrent
  double-persist of the same key - something no in-memory fake can prove
  on its own (mirrors `test_paper_execution_gateway.py`'s
  `UNIQUE (proposal_id)` concurrency proof exactly); and
- that a strategy-authored proposal (`created_by IS NULL`,
  `strategy_id` set) is not a second, weaker path into execution:
  the *same* `atp_exec_paper.gateway.run_once`, under the *same*
  `atp_paper_exec` role, evaluates it and can mint a real
  `ApprovedOrderIntent`/`Order`/`Fill`, exactly as it does for a
  human-submitted one.

`fixture_momentum.FixtureMomentumStrategy` always proposes `MARKET`
orders (Milestone 2C, deliberately trivial) - and `MARKET` proposals are
*always* rejected by the risk engine before ever reaching the simulator
(`execution/paper/src/atp_exec_paper/simulator.py`'s own docstring: "the
risk engine rejects it first... no canonical price"). Proving the full
`TradeProposal -> RiskDecision -> ApprovedOrderIntent -> Order -> Fill`
chain for a strategy-authored proposal therefore needs a hand-inserted
`LIMIT` proposal carrying the same `created_by IS NULL`/`strategy_id`
attribution shape the real strategy would produce, inserted through the
real `atp_strategy` role - see
`test_strategy_authored_limit_proposal_is_approved_and_filled_end_to_end`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_domain.money import Quantity
from atp_domain.strategy import ProposedTrade, StrategyRegistry, derive_strategy_id
from atp_domain.types import InstrumentId, OrderType, Product, Side
from atp_exec_paper.gateway import run_once as exec_paper_run_once
from atp_persistence.db import make_session_factory
from atp_strategy.proposals import build_trade_proposal as _build_trade_proposal
from atp_strategy.proposals import derive_client_request_id as _derive_client_request_id
from atp_strategy.proposals import persist_proposed_trade
from atp_strategy.proposals import resolve_cycle_epoch as _resolve_cycle_epoch
from atp_strategy.runner import run_once as strategy_run_once
from atp_strategy.strategies.fixture_momentum import STRATEGY_KEY as _FIXTURE_STRATEGY_KEY
from atp_strategy.strategies.fixture_momentum import (
    STRATEGY_VERSION as _FIXTURE_STRATEGY_VERSION,
)
from atp_strategy.strategies.fixture_momentum import FixtureMomentumStrategy
from atp_strategy.uow import strategy_unit_of_work_factory

_FIXTURE_STRATEGY_ID = derive_strategy_id(_FIXTURE_STRATEGY_KEY)


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_async_psycopg_url(dsn: str) -> str:
    """Mirrors every other integration test file's own copy of this
    helper - no shared module exists for it."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


def _fresh_registry() -> StrategyRegistry:
    """A fresh registry per test, not the process-wide
    `DEFAULT_STRATEGY_REGISTRY` - `StrategyRegistry.register` raises on a
    second registration of the same `strategy_key`, so each test builds
    its own single-strategy registry rather than risking a collision with
    whatever else imported `atp_strategy.registry` in this process."""
    registry = StrategyRegistry()
    registry.register(FixtureMomentumStrategy())
    return registry


#: Reverse FK dependency order for everything a strategy-authored
#: `paper.trade_proposals` row can end up owning once `atp_exec_paper`
#: has evaluated it - mirrors `tests/integration/db/conftest.py`'s
#: `_DELETE_USER_CASCADE_STATEMENTS`, keyed on `strategy_id` instead of
#: `created_by` (a strategy-authored proposal has `created_by IS NULL` by
#: construction, ADR-015, so the user-keyed cascade cannot reach it).
#:
#: `audit.audit_events` is deliberately absent, for exactly the reason
#: `delete_user_cascade` states for its own omission: the ledger is
#: append-only (ADR-010) and its `audit_events_append_only` trigger
#: refuses DELETE even for `atp_owner`. Audit rows carry no FK to
#: `paper.trade_proposals`, so they never block this cascade - which is
#: also why every audit assertion in this module is written as a delta or
#: time-window, never an absolute count.
_DELETE_STRATEGY_PROPOSAL_CASCADE_STATEMENTS = (
    """
    DELETE FROM paper.cash_ledger WHERE related_fill_id IN (
        SELECT f.fill_id FROM paper.fills f
        JOIN paper.orders o ON o.internal_order_id = f.internal_order_id
        JOIN paper.trade_proposals tp ON tp.proposal_id = o.proposal_id
        WHERE tp.strategy_id = %s)
    """,
    """
    DELETE FROM paper.fills WHERE internal_order_id IN (
        SELECT o.internal_order_id FROM paper.orders o
        JOIN paper.trade_proposals tp ON tp.proposal_id = o.proposal_id
        WHERE tp.strategy_id = %s)
    """,
    """
    DELETE FROM paper.orders WHERE proposal_id IN (
        SELECT proposal_id FROM paper.trade_proposals WHERE strategy_id = %s)
    """,
    """
    DELETE FROM paper.order_intents WHERE decision_id IN (
        SELECT rd.decision_id FROM paper.risk_decisions rd
        JOIN paper.trade_proposals tp ON tp.proposal_id = rd.proposal_id
        WHERE tp.strategy_id = %s)
    """,
    """
    DELETE FROM paper.risk_decisions WHERE proposal_id IN (
        SELECT proposal_id FROM paper.trade_proposals WHERE strategy_id = %s)
    """,
    "DELETE FROM paper.trade_proposals WHERE strategy_id = %s",
)


def _cleanup_fixture_strategy_rows(conn: psycopg.Connection) -> None:
    """Removes every `paper.trade_proposals` row attributed to the
    fixture-momentum strategy, plus everything referencing one.
    `strategy_id` is a deterministic derivation of a fixed, never-reused
    test strategy key (`fixture-momentum-v1`), so filtering on it is as
    safe a cleanup key as `seeded_user_id`-based cleanup elsewhere in this
    suite - no other test in this process ever writes a row with this
    `strategy_id`.

    Called both before and after each test that runs the real runner, so a
    prior crashed run can never leave a stale row that makes this run's
    `UNIQUE(client_request_id)` silently treat a genuine new cycle as a
    replay."""
    with conn.cursor() as cur:
        for statement in _DELETE_STRATEGY_PROPOSAL_CASCADE_STATEMENTS:
            cur.execute(statement, (_FIXTURE_STRATEGY_ID,))
    conn.commit()


def _count_strategy_audit_events(conn: psycopg.Connection) -> int:
    """Absolute count of fixture-strategy audit events. Only ever used to
    compute a *delta* across one action - `audit.audit_events` is
    append-only, so rows from earlier tests in the same session
    legitimately persist and an absolute assertion would be
    order-dependent."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM audit.audit_events WHERE strategy_id = %s",
            (_FIXTURE_STRATEGY_ID,),
        )
        (count,) = cur.fetchone()  # type: ignore[misc]
    conn.rollback()
    return int(count)


def _seed_strategy_switch(conn: psycopg.Connection, strategy_key: str, *, engaged: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM core.kill_switch_state WHERE switch_id = %s", (f"STRATEGY:{strategy_key}",)
        )
        cur.execute(
            "INSERT INTO core.kill_switch_state (switch_id, engaged, updated_at, updated_by, reason) "
            "VALUES (%s, %s, now(), NULL, NULL)",
            (f"STRATEGY:{strategy_key}", engaged),
        )
    conn.commit()


def _delete_strategy_switch(conn: psycopg.Connection, strategy_key: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM core.kill_switch_state WHERE switch_id = %s", (f"STRATEGY:{strategy_key}",)
        )
    conn.commit()


@pytest.fixture
def seeded_instrument_id(migrated_database: str, owner_connection: psycopg.Connection) -> str:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT instrument_id FROM core.instruments WHERE provider = 'FIXTURE' LIMIT 1")
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None, "expected at least one seeded FIXTURE instrument"
    return str(row[0])


@pytest.fixture
def cleanup_fixture_strategy_rows(migrated_database: str, owner_connection: psycopg.Connection):
    _cleanup_fixture_strategy_rows(owner_connection)
    yield
    _cleanup_fixture_strategy_rows(owner_connection)


@pytest.fixture
def disengaged_fixture_strategy_switch(
    migrated_database: str, owner_connection: psycopg.Connection
):
    """`migrated_database` is depended on explicitly (not merely assumed
    from the requesting test's own signature) so `core.kill_switch_state`
    is guaranteed to exist before this fixture writes to it, regardless of
    the order a future test lists its fixtures in."""
    _seed_strategy_switch(owner_connection, _FIXTURE_STRATEGY_KEY, engaged=False)
    yield
    _delete_strategy_switch(owner_connection, _FIXTURE_STRATEGY_KEY)


# --- Kill-switch gating against real PostgreSQL -----------------------------


def test_missing_kill_switch_row_blocks_the_real_strategy_and_creates_no_proposal(
    migrated_database: str,
    owner_dsn: str,
    strategy_dsn: str,
    owner_connection: psycopg.Connection,
    cleanup_fixture_strategy_rows: None,
) -> None:
    """STRATEGY:fixture-momentum-v1 has no row at all -> UNAVAILABLE ->
    blocking, identically to ENGAGED (ADR-014 §B's fail-closed default)."""
    _delete_strategy_switch(owner_connection, _FIXTURE_STRATEGY_KEY)

    async def run() -> bool:
        engine = create_async_engine(_as_async_psycopg_url(strategy_dsn))
        try:
            uow_factory = strategy_unit_of_work_factory(make_session_factory(engine))
            return await strategy_run_once(
                uow_factory,
                registry=_fresh_registry(),
                id_generator=UUIDv7Generator(),
                clock=UTCClock(),
            )
        finally:
            await engine.dispose()

    found = asyncio.run(run())
    assert found is True  # a strategy was registered - it was just skipped

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM paper.trade_proposals WHERE strategy_id = %s",
            (_FIXTURE_STRATEGY_ID,),
        )
        (count,) = cur.fetchone()  # type: ignore[misc]
    owner_connection.rollback()
    assert count == 0


def test_engaged_kill_switch_blocks_the_real_strategy_and_creates_no_proposal(
    migrated_database: str,
    owner_dsn: str,
    strategy_dsn: str,
    owner_connection: psycopg.Connection,
    cleanup_fixture_strategy_rows: None,
) -> None:
    _seed_strategy_switch(owner_connection, _FIXTURE_STRATEGY_KEY, engaged=True)
    try:

        async def run() -> None:
            engine = create_async_engine(_as_async_psycopg_url(strategy_dsn))
            try:
                uow_factory = strategy_unit_of_work_factory(make_session_factory(engine))
                await strategy_run_once(
                    uow_factory,
                    registry=_fresh_registry(),
                    id_generator=UUIDv7Generator(),
                    clock=UTCClock(),
                )
            finally:
                await engine.dispose()

        asyncio.run(run())

        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM paper.trade_proposals WHERE strategy_id = %s",
                (_FIXTURE_STRATEGY_ID,),
            )
            (count,) = cur.fetchone()  # type: ignore[misc]
        owner_connection.rollback()
        assert count == 0
    finally:
        _delete_strategy_switch(owner_connection, _FIXTURE_STRATEGY_KEY)


# --- Real strategy execution path -------------------------------------------


def test_disengaged_kill_switch_allows_the_real_strategy_to_run_and_persist(
    migrated_database: str,
    owner_dsn: str,
    strategy_dsn: str,
    owner_connection: psycopg.Connection,
    cleanup_fixture_strategy_rows: None,
    disengaged_fixture_strategy_switch: None,
) -> None:
    """The real `atp_strategy.runner.run_once`, against real
    `core.instruments`/`core.kill_switch_state` reads and a real
    `paper.trade_proposals`/`audit.audit_events` write, under the actual
    `atp_strategy` role. Proves identity/version/created_by/mode and that
    the audit event committed atomically with its proposal."""
    # `audit.audit_events` is append-only (ADR-010) - rows written by an
    # earlier test in this session cannot be cleaned up, so the audit
    # assertions below are scoped to events recorded at or after this
    # instant rather than to an absolute count. `recorded_at` is set by
    # the application's own injected `UTCClock` inside this very process
    # (never by the database), so it is directly comparable to this value.
    started_at = datetime.now(UTC)

    async def run() -> bool:
        engine = create_async_engine(_as_async_psycopg_url(strategy_dsn))
        try:
            uow_factory = strategy_unit_of_work_factory(make_session_factory(engine))
            return await strategy_run_once(
                uow_factory,
                registry=_fresh_registry(),
                id_generator=UUIDv7Generator(),
                clock=UTCClock(),
            )
        finally:
            await engine.dispose()

    found = asyncio.run(run())
    assert found is True

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT proposal_id, mode, created_by, strategy_id, strategy_version, "
            "order_type, client_request_id "
            "FROM paper.trade_proposals WHERE strategy_id = %s ORDER BY created_at",
            (_FIXTURE_STRATEGY_ID,),
        )
        proposal_rows = cur.fetchall()
        cur.execute(
            "SELECT actor_type, actor_id, action, mode, strategy_id, strategy_version "
            "FROM audit.audit_events WHERE strategy_id = %s AND recorded_at >= %s",
            (_FIXTURE_STRATEGY_ID, started_at),
        )
        audit_rows = cur.fetchall()
    owner_connection.rollback()

    # core.instruments seeds >= 20 FIXTURE rows (migration 0002) and every
    # one of them clears fixture-momentum's trivial synthetic-price
    # threshold (Milestone 2C) - so one real evaluation cycle produces one
    # proposal per active instrument, proving "multiple proposals from the
    # same evaluation are independently persisted" against real Postgres
    # with zero additional test scaffolding.
    assert len(proposal_rows) > 1
    assert len(audit_rows) == len(proposal_rows)

    for (
        _proposal_id,
        mode,
        created_by,
        strategy_id,
        strategy_version,
        order_type,
        client_request_id,
    ) in proposal_rows:
        assert mode == "PAPER"
        assert created_by is None
        assert str(strategy_id) == str(_FIXTURE_STRATEGY_ID)
        assert strategy_version == _FIXTURE_STRATEGY_VERSION
        assert order_type == "MARKET"
        assert client_request_id.startswith(
            f"{_FIXTURE_STRATEGY_KEY}:v{_FIXTURE_STRATEGY_VERSION}:"
        )

    client_request_ids = {row[6] for row in proposal_rows}
    assert len(client_request_ids) == len(proposal_rows)  # every one distinct

    for actor_type, actor_id, action, mode, strategy_id, strategy_version in audit_rows:
        assert actor_type == "AGENT"
        assert actor_id == f"strategy/{_FIXTURE_STRATEGY_KEY}"
        assert action == "PROPOSAL_CREATED"
        assert mode == "PAPER"
        assert str(strategy_id) == str(_FIXTURE_STRATEGY_ID)
        assert strategy_version == _FIXTURE_STRATEGY_VERSION


def test_real_strategy_market_proposal_is_evaluated_by_the_real_risk_engine_not_bypassed(
    migrated_database: str,
    owner_dsn: str,
    strategy_dsn: str,
    paper_exec_dsn: str,
    owner_connection: psycopg.Connection,
    cleanup_fixture_strategy_rows: None,
    disengaged_fixture_strategy_switch: None,
) -> None:
    """The real strategy's real (MARKET) proposal, run through
    `atp_exec_paper.gateway.run_once` under the real `atp_paper_exec`
    role - the same, unmodified execution path a human proposal takes,
    proving a strategy-authored proposal is not bypassed. MARKET is
    deterministically rejected before the simulator (see module
    docstring), so `REJECTED`/no order is the expected, not an
    ambiguous, outcome here."""

    async def run_strategy() -> None:
        engine = create_async_engine(_as_async_psycopg_url(strategy_dsn))
        try:
            uow_factory = strategy_unit_of_work_factory(make_session_factory(engine))
            await strategy_run_once(
                uow_factory,
                registry=_fresh_registry(),
                id_generator=UUIDv7Generator(),
                clock=UTCClock(),
            )
        finally:
            await engine.dispose()

    asyncio.run(run_strategy())

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT proposal_id FROM paper.trade_proposals WHERE strategy_id = %s LIMIT 1",
            (_FIXTURE_STRATEGY_ID,),
        )
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None
    proposal_id = str(row[0])

    async def run_exec() -> object:
        engine = create_async_engine(_as_async_psycopg_url(paper_exec_dsn))
        try:
            return await exec_paper_run_once(
                make_session_factory(engine),
                proposal_id,
                id_generator=UUIDv7Generator(),
                clock=UTCClock(),
            )
        finally:
            await engine.dispose()

    outcome = asyncio.run(run_exec())
    assert outcome.already_claimed is False  # type: ignore[attr-defined]
    assert outcome.decision_outcome is not None  # type: ignore[attr-defined]
    assert outcome.decision_outcome.value == "REJECTED"  # type: ignore[attr-defined]
    assert outcome.order_id is None  # type: ignore[attr-defined]

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM paper.risk_decisions WHERE proposal_id = %s", (proposal_id,)
        )
        (decision_count,) = cur.fetchone()  # type: ignore[misc]
        cur.execute("SELECT count(*) FROM paper.orders WHERE proposal_id = %s", (proposal_id,))
        (order_count,) = cur.fetchone()  # type: ignore[misc]
    owner_connection.rollback()
    assert decision_count == 1  # the strategy-authored proposal genuinely reached the risk engine
    assert order_count == 0


def test_strategy_authored_limit_proposal_is_approved_and_filled_end_to_end(
    owner_dsn: str,
    strategy_dsn: str,
    paper_exec_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
) -> None:
    """The full `TradeProposal -> RiskDecision -> ApprovedOrderIntent ->
    Order -> Fill` chain, for a proposal carrying the exact attribution
    shape the real strategy produces (`created_by IS NULL`, `strategy_id`
    set) - inserted through the real `atp_strategy` role's own `INSERT`
    grant, not `atp_owner`. Uses `LIMIT`/quantity=10/limit_price=100,
    mirroring `test_paper_execution_gateway.py`'s own proven-approved
    shape exactly (well under the seeded PAPER risk config's
    1,000,000 max order notional)."""
    proposal_id = _new_uuid()
    client_request_id = f"it-{proposal_id}"
    with psycopg.connect(strategy_dsn, connect_timeout=5) as strategy_conn:
        with strategy_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type, limit_price,
                     trigger_price, product, client_request_id, expected_risk, created_by,
                     strategy_id, strategy_version, created_at)
                VALUES
                    (%s, 'PAPER', %s, 'BUY', 10, 'LIMIT', 100, NULL, 'CNC', %s, '{}', NULL, %s, 1, now())
                """,
                (proposal_id, seeded_instrument_id, client_request_id, str(_FIXTURE_STRATEGY_ID)),
            )
        strategy_conn.commit()

    try:

        async def run_exec() -> object:
            engine = create_async_engine(_as_async_psycopg_url(paper_exec_dsn))
            try:
                return await exec_paper_run_once(
                    make_session_factory(engine),
                    proposal_id,
                    id_generator=UUIDv7Generator(),
                    clock=UTCClock(),
                )
            finally:
                await engine.dispose()

        outcome = asyncio.run(run_exec())
        assert outcome.already_claimed is False  # type: ignore[attr-defined]
        assert outcome.order_id is not None  # type: ignore[attr-defined]

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
            cur.execute(
                "SELECT count(*) FROM paper.order_intents oi "
                "JOIN paper.risk_decisions rd ON rd.decision_id = oi.decision_id "
                "WHERE rd.proposal_id = %s",
                (proposal_id,),
            )
            (intent_count,) = cur.fetchone()  # type: ignore[misc]
        owner_connection.rollback()

        assert order_row is not None
        assert order_row[0] == "FILLED"
        assert fill_row is not None
        assert fill_row[0] is True
        assert fill_row[1] == "PAPER_SIMULATOR"
        assert intent_count == 1  # a real ApprovedOrderIntent was minted for this proposal
    finally:
        with owner_connection.cursor() as cur:
            cur.execute(
                "DELETE FROM paper.cash_ledger WHERE related_fill_id IN "
                "(SELECT fill_id FROM paper.fills f "
                " JOIN paper.orders o ON o.internal_order_id = f.internal_order_id "
                " WHERE o.proposal_id = %s)",
                (proposal_id,),
            )
            cur.execute(
                "DELETE FROM paper.fills WHERE internal_order_id IN "
                "(SELECT internal_order_id FROM paper.orders WHERE proposal_id = %s)",
                (proposal_id,),
            )
            cur.execute("DELETE FROM paper.orders WHERE proposal_id = %s", (proposal_id,))
            cur.execute(
                "DELETE FROM paper.order_intents WHERE decision_id IN "
                "(SELECT decision_id FROM paper.risk_decisions WHERE proposal_id = %s)",
                (proposal_id,),
            )
            cur.execute("DELETE FROM paper.risk_decisions WHERE proposal_id = %s", (proposal_id,))
            cur.execute("DELETE FROM paper.trade_proposals WHERE proposal_id = %s", (proposal_id,))
        owner_connection.commit()


# --- Concurrency / idempotency -----------------------------------------------


def test_concurrent_persist_of_the_same_client_request_id_creates_exactly_one_proposal(
    owner_dsn: str,
    strategy_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
) -> None:
    """The real proof of Milestone 2C's idempotency design: two genuinely
    concurrent `persist_proposed_trade` calls for the identical
    `(strategy_key, strategy_version, cycle_epoch, instrument_key,
    ordinal)` tuple, over two separate engines/connections - the
    database's own `UNIQUE (client_request_id)` constraint on
    `paper.trade_proposals` is what makes exactly one of them win.
    Mirrors `test_paper_execution_gateway.py`'s
    `UNIQUE (proposal_id)` concurrency proof exactly."""
    proposed = ProposedTrade(
        instrument_id=InstrumentId(seeded_instrument_id),
        side=Side.BUY,
        quantity=Quantity(Decimal(1)),
        order_type=OrderType.MARKET,
        limit_price=None,
        product=Product.CNC,
    )
    client_request_id = f"it-concurrent-{_new_uuid()}"
    now = datetime.now(UTC)

    def _make_proposal() -> object:
        return _build_trade_proposal(
            proposed,
            strategy=FixtureMomentumStrategy(),
            client_request_id=client_request_id,
            proposal_id=_new_uuid(),  # type: ignore[arg-type]
            created_at=now,
        )

    async def run() -> list[bool]:
        engine_a = create_async_engine(_as_async_psycopg_url(strategy_dsn))
        engine_b = create_async_engine(_as_async_psycopg_url(strategy_dsn))
        try:
            uow_factory_a = strategy_unit_of_work_factory(make_session_factory(engine_a))
            uow_factory_b = strategy_unit_of_work_factory(make_session_factory(engine_b))
            results = await asyncio.gather(
                persist_proposed_trade(
                    uow_factory_a,
                    _make_proposal(),  # type: ignore[arg-type]
                    strategy_key=_FIXTURE_STRATEGY_KEY,
                    id_generator=UUIDv7Generator(),
                    clock=UTCClock(),
                    correlation_id=_new_uuid(),
                ),
                persist_proposed_trade(
                    uow_factory_b,
                    _make_proposal(),  # type: ignore[arg-type]
                    strategy_key=_FIXTURE_STRATEGY_KEY,
                    id_generator=UUIDv7Generator(),
                    clock=UTCClock(),
                    correlation_id=_new_uuid(),
                ),
            )
            return list(results)
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

    audit_count_before = _count_strategy_audit_events(owner_connection)
    try:
        inserted_flags = asyncio.run(run())
        assert inserted_flags.count(True) == 1
        assert inserted_flags.count(False) == 1

        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM paper.trade_proposals WHERE client_request_id = %s",
                (client_request_id,),
            )
            (proposal_count,) = cur.fetchone()  # type: ignore[misc]
        owner_connection.rollback()
        assert proposal_count == 1  # exactly one winner, the loser's transaction rolled back

        # Exactly one audit event committed - the loser's transaction
        # (proposal INSERT + audit INSERT, one transaction) rolled back
        # in full, proving atomicity: a duplicate never leaves an
        # orphaned audit row behind. A delta, not an absolute count:
        # `audit.audit_events` is append-only, so this test cannot (and
        # must not) clean up the winner's row afterwards.
        assert _count_strategy_audit_events(owner_connection) - audit_count_before == 1
    finally:
        with owner_connection.cursor() as cur:
            cur.execute(
                "DELETE FROM paper.trade_proposals WHERE client_request_id = %s",
                (client_request_id,),
            )
        owner_connection.commit()


def test_duplicate_persist_does_not_roll_back_a_distinct_proposal_persisted_after_it(
    owner_dsn: str,
    strategy_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
) -> None:
    """Transaction independence: the duplicate's own transaction rolls
    back (real `IntegrityError`, real ROLLBACK), but a distinct
    `client_request_id` persisted immediately afterward - its own,
    separate transaction - is entirely unaffected."""
    proposed = ProposedTrade(
        instrument_id=InstrumentId(seeded_instrument_id),
        side=Side.BUY,
        quantity=Quantity(Decimal(1)),
        order_type=OrderType.MARKET,
        limit_price=None,
        product=Product.CNC,
    )
    now = datetime.now(UTC)
    request_id_a = f"it-txn-a-{_new_uuid()}"
    request_id_b = f"it-txn-b-{_new_uuid()}"

    def _proposal(client_request_id: str) -> object:
        return _build_trade_proposal(
            proposed,
            strategy=FixtureMomentumStrategy(),
            client_request_id=client_request_id,
            proposal_id=_new_uuid(),  # type: ignore[arg-type]
            created_at=now,
        )

    async def persist_once(uow_factory: object, client_request_id: str) -> bool:
        return await persist_proposed_trade(
            uow_factory,  # type: ignore[arg-type]
            _proposal(client_request_id),  # type: ignore[arg-type]
            strategy_key=_FIXTURE_STRATEGY_KEY,
            id_generator=UUIDv7Generator(),
            clock=UTCClock(),
            correlation_id=_new_uuid(),
        )

    async def run() -> tuple[bool, bool, bool]:
        engine = create_async_engine(_as_async_psycopg_url(strategy_dsn))
        try:
            uow_factory = strategy_unit_of_work_factory(make_session_factory(engine))
            first = await persist_once(uow_factory, request_id_a)
            duplicate = await persist_once(uow_factory, request_id_a)
            second_distinct = await persist_once(uow_factory, request_id_b)
            return first, duplicate, second_distinct
        finally:
            await engine.dispose()

    try:
        first, duplicate, second_distinct = asyncio.run(run())
        assert first is True
        assert duplicate is False
        assert second_distinct is True

        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT client_request_id FROM paper.trade_proposals "
                "WHERE client_request_id = ANY(%s) ORDER BY client_request_id",
                ([request_id_a, request_id_b],),
            )
            rows = [row[0] for row in cur.fetchall()]
        owner_connection.rollback()
        assert rows == sorted([request_id_a, request_id_b])
    finally:
        # `audit.audit_events` is intentionally not cleaned up - it is
        # append-only (ADR-010) and its trigger refuses DELETE even for
        # `atp_owner`. Audit rows carry no FK to `paper.trade_proposals`,
        # so they never block this cleanup.
        with owner_connection.cursor() as cur:
            cur.execute(
                "DELETE FROM paper.trade_proposals WHERE client_request_id = ANY(%s)",
                ([request_id_a, request_id_b],),
            )
        owner_connection.commit()


def test_derive_client_request_id_produces_distinct_ids_across_cycles_versions_and_instruments() -> (
    None
):
    """No database needed - pure verification that the deterministic
    derivation Milestone 2C specified actually produces distinct keys
    across every dimension the concurrency/idempotency requirement names,
    run here alongside the real-DB proofs above for one consolidated
    Milestone 2D report."""
    base = _derive_client_request_id(
        strategy_key="fixture-momentum-v1",
        strategy_version=1,
        cycle_epoch=60,
        instrument_key="AAA",
        ordinal=0,
    )
    different_cycle = _derive_client_request_id(
        strategy_key="fixture-momentum-v1",
        strategy_version=1,
        cycle_epoch=120,
        instrument_key="AAA",
        ordinal=0,
    )
    different_version = _derive_client_request_id(
        strategy_key="fixture-momentum-v1",
        strategy_version=2,
        cycle_epoch=60,
        instrument_key="AAA",
        ordinal=0,
    )
    different_instrument = _derive_client_request_id(
        strategy_key="fixture-momentum-v1",
        strategy_version=1,
        cycle_epoch=60,
        instrument_key="BBB",
        ordinal=0,
    )
    ids = {base, different_cycle, different_version, different_instrument}
    assert len(ids) == 4

    now = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
    epoch_a = _resolve_cycle_epoch(now, evaluation_interval_seconds=60.0)
    epoch_b = _resolve_cycle_epoch(now, evaluation_interval_seconds=60.0)
    assert epoch_a == epoch_b  # repeated evaluation within one cycle is stable
