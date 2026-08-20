"""Phase 1 Step 10 (ADR-012): PAPER trade-proposal intake against a real,
migrated database, using the actual `atp_api` role, plus the full
intake -> `atp_exec_paper.run_once` -> ledger-read loop.

`tests/unit/api/test_paper_proposals.py`/`test_paper_ledger.py` already
exercise this logic extensively against in-memory fakes; this file's job is
narrower: prove `atp_api`'s actual (unwidened) grants are sufficient for the
real `INSERT`, prove `UNIQUE (client_request_id)` genuinely arbitrates a
concurrent double-submit - something no in-memory fake can prove on its
own - and prove the whole pipeline this milestone exists to unblock
(ADR-011's claim loop finally has a real producer).
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from atp_api.app import create_app
from atp_api.config import ApiSettings
from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_PAPER_TRADER
from atp_api.services import paper_proposals
from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_exec_paper.gateway import run_once
from atp_persistence.db import make_session_factory, read_only_session, unit_of_work
from atp_persistence.repositories import SqlAlchemyInstrumentRepository
from atp_platform.config import Settings
from tests.integration.db.conftest import delete_user_cascade


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_async_psycopg_url(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


def _build_client(api_dsn: str) -> TestClient:
    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url=_as_async_psycopg_url(api_dsn),  # type: ignore[arg-type]
        redis_url="redis://:x@localhost:6379/0",  # type: ignore[arg-type]
    )
    engine = create_async_engine(_as_async_psycopg_url(api_dsn))
    session_factory = make_session_factory(engine)
    app = create_app(settings=settings, api_settings=ApiSettings(), session_factory=session_factory)
    # See test_auth_flows.py: Starlette's default client host
    # ("testclient") is not a valid value for `core.sessions.ip_address`,
    # a Postgres INET column - login fails with DataError -> 503.
    return TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50000))


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
def seeded_trader(migrated_database: str, owner_connection: psycopg.Connection):
    user_id = _new_uuid()
    username = f"itest-trader-{user_id}"
    password = "a real password"
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.users
                (user_id, username, password_hash, role, is_active, must_change_password,
                 created_at, updated_at)
            VALUES (%s, %s, %s, %s, true, false, now(), now())
            """,
            (user_id, username, hash_password(password), ROLE_PAPER_TRADER),
        )
    owner_connection.commit()
    yield user_id, username, password
    # The shared FK-ordered helper, not a local DELETE pair: this fixture's
    # tests run the full intake -> risk-decision -> order path, so a
    # proposal is referenced by `paper.risk_decisions` by the time teardown
    # runs and a direct `DELETE FROM paper.trade_proposals` raises
    # `ForeignKeyViolation` (Phase 1 Step 12 Phase A).
    delete_user_cascade(owner_connection, user_id)


def _login(client: TestClient, *, username: str, password: str) -> None:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def test_submit_proposal_round_trips_through_the_real_atp_api_role(
    api_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_trader: tuple[str, str, str],
) -> None:
    user_id, username, password = seeded_trader
    client = _build_client(api_dsn)
    _login(client, username=username, password=password)

    csrf_cookie = client.cookies.get("atp_csrf")
    response = client.post(
        "/api/v1/paper/proposals",
        json={
            "instrument_id": seeded_instrument_id,
            "side": "BUY",
            "quantity": "10",
            "order_type": "LIMIT",
            "limit_price": "100",
            "product": "CNC",
            "client_request_id": f"itest-{_new_uuid()}",
        },
        headers={"x-csrf-token": csrf_cookie},
    )

    assert response.status_code == 202
    proposal_id = response.json()["proposal_id"]

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT mode, created_by FROM paper.trade_proposals WHERE proposal_id = %s",
            (proposal_id,),
        )
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None
    assert row[0] == "PAPER"
    # `str(...)`: read through raw psycopg (not the ORM), a Postgres
    # `uuid` column comes back as a Python `UUID` object, while `user_id`
    # is the plain `str` every domain identifier uses. The ORM path is
    # unaffected - `uuid_pk`/`uuid_column` set `as_uuid=False` - so this is
    # a raw-SQL test-side normalization, not a repository behavior change.
    assert str(row[1]) == user_id


def test_duplicate_client_request_id_under_genuine_concurrency_creates_exactly_one_proposal(
    api_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_trader: tuple[str, str, str],
) -> None:
    """The real proof of the idempotency claim: two genuinely concurrent
    `submit_proposal` calls with the same `client_request_id`, over two
    separate engines/connections - `paper.trade_proposals`' own
    `UNIQUE (client_request_id)` constraint is what makes exactly one
    proposal exist afterward, mirroring
    `test_paper_execution_gateway.py::test_concurrent_execution_of_the_same_proposal_creates_exactly_one_order`'s
    approach for ADR-011's claim exclusivity."""
    user_id, _username, _password = seeded_trader
    client_request_id = f"itest-race-{_new_uuid()}"

    async def submit_once(engine) -> paper_proposals.SubmitProposalResult:
        session_factory = make_session_factory(engine)
        # Mirrors atp_api.deps' real composition: the instrument existence
        # check runs on a separate read-only session from the UnitOfWork
        # the insert itself uses (deps.py's module docstring).
        async with read_only_session(session_factory) as read_session:
            instruments = SqlAlchemyInstrumentRepository(read_session)
            async with unit_of_work(session_factory) as uow:
                return await paper_proposals.submit_proposal(
                    uow,
                    instruments,
                    instrument_id=seeded_instrument_id,
                    side="BUY",
                    quantity=Decimal("10"),
                    order_type="LIMIT",
                    limit_price=Decimal("100"),
                    product="CNC",
                    client_request_id=client_request_id,
                    expected_risk={},
                    created_by=user_id,
                    correlation_id=str(uuid.uuid4()),
                    clock=UTCClock(),
                    id_generator=UUIDv7Generator(),
                )

    async def run() -> list[paper_proposals.SubmitProposalResult]:
        engine_a = create_async_engine(_as_async_psycopg_url(api_dsn))
        engine_b = create_async_engine(_as_async_psycopg_url(api_dsn))
        try:
            return await asyncio.gather(submit_once(engine_a), submit_once(engine_b))
        finally:
            await engine_a.dispose()
            await engine_b.dispose()

    results = asyncio.run(run())
    assert results[0].proposal_id == results[1].proposal_id
    assert [r.is_replay for r in results].count(True) == 1

    with owner_connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM paper.trade_proposals WHERE client_request_id = %s",
            (client_request_id,),
        )
        (count,) = cur.fetchone()  # type: ignore[misc]
    owner_connection.rollback()
    assert count == 1


def test_full_loop_intake_then_gateway_then_ledger_read(
    api_dsn: str,
    paper_exec_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_trader: tuple[str, str, str],
) -> None:
    """The end-to-end proof this milestone exists for: a proposal submitted
    through `atp_api` is picked up, unmodified, by `atp_exec_paper`'s
    ADR-011 claim loop, and the resulting fill/position/cash movement is
    readable back through `atp_api`'s own ledger routes."""
    _user_id, username, password = seeded_trader
    client = _build_client(api_dsn)
    _login(client, username=username, password=password)

    csrf_cookie = client.cookies.get("atp_csrf")
    submit_response = client.post(
        "/api/v1/paper/proposals",
        json={
            "instrument_id": seeded_instrument_id,
            "side": "BUY",
            "quantity": "10",
            "order_type": "LIMIT",
            "limit_price": "100",
            "product": "CNC",
            "client_request_id": f"itest-full-loop-{_new_uuid()}",
        },
        headers={"x-csrf-token": csrf_cookie},
    )
    assert submit_response.status_code == 202
    proposal_id = submit_response.json()["proposal_id"]

    async def run_gateway() -> None:
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
        finally:
            await engine.dispose()

    asyncio.run(run_gateway())

    detail_response = client.get(f"/api/v1/paper/proposals/{proposal_id}")
    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["decision"]["outcome"] == "APPROVED"
    assert body["order"]["status"] == "FILLED"
    assert body["fill"]["simulated"] is True

    positions_response = client.get("/api/v1/paper/positions")
    assert positions_response.status_code == 200
    assert any(
        p["instrument_id"] == seeded_instrument_id for p in positions_response.json()["items"]
    )

    cash_response = client.get("/api/v1/paper/cash")
    assert cash_response.status_code == 200
    assert cash_response.json()["balance"] is not None


# ---------------------------------------------------------------------------
# Phase 1 Step 17: paper_ledger.list_proposals's N+1 fix
# ---------------------------------------------------------------------------


def _build_client_with_engine(api_dsn: str) -> tuple[TestClient, AsyncEngine]:
    """Like `_build_client`, but also returns the `AsyncEngine` so a test
    can attach a query-counting listener to it - `create_app`'s own
    `app.state.engine` stays `None` when an explicit `session_factory` is
    passed in (`atp_api.app.create_app`'s docstring), so it cannot be
    recovered from the built app afterward."""
    settings = Settings(
        session_secret_key="a" * 40,  # type: ignore[arg-type]
        database_url=_as_async_psycopg_url(api_dsn),  # type: ignore[arg-type]
        redis_url="redis://:x@localhost:6379/0",  # type: ignore[arg-type]
    )
    engine = create_async_engine(_as_async_psycopg_url(api_dsn))
    session_factory = make_session_factory(engine)
    app = create_app(settings=settings, api_settings=ApiSettings(), session_factory=session_factory)
    client = TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50000))
    return client, engine


def _insert_bare_proposal(
    owner_connection: psycopg.Connection,
    *,
    proposal_id: str,
    instrument_id: str,
    created_by: str,
    client_request_id: str,
) -> None:
    """A `paper.trade_proposals` row only - no decision/order/fill. The
    N+1 fix's query-count claim holds regardless of whether those child
    rows exist (an absent decision/order is a `None` in the batched
    mapping, not a skipped query), so seeding proposals alone is enough to
    prove it without threading through `order_intents`' own FK chain."""
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper.trade_proposals
                (proposal_id, mode, instrument_id, side, quantity, order_type, limit_price,
                 product, client_request_id, expected_risk, created_by, created_at)
            VALUES (%s, 'PAPER', %s, 'BUY', 10, 'LIMIT', 100, 'CNC', %s, '{}'::jsonb, %s, now())
            """,
            (proposal_id, instrument_id, client_request_id, created_by),
        )
    owner_connection.commit()


def _count_ledger_batch_queries(
    client: TestClient, engine: AsyncEngine, *, limit: int
) -> dict[str, int]:
    """Counts real SQL statements against `paper.risk_decisions`/
    `paper.orders`/`paper.fills` issued while serving one
    `GET /api/v1/paper/proposals` request - the thing no in-memory-fake
    call-count assertion (`tests/unit/api/test_paper_ledger.py`) can prove
    on its own: that the batched repository methods really do compile to
    one `SELECT ... WHERE ... IN (...)` each under the real `atp_api`
    role, not one query per proposal. Ignores every other statement
    (session/user lookups, the proposals list query itself, connection
    setup) so the assertion is not brittle against unrelated queries this
    request also legitimately issues."""
    counts = {"risk_decisions": 0, "orders": 0, "fills": 0}

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
        lowered = statement.lower()
        for table in counts:
            if f"paper.{table}" in lowered:
                counts[table] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)
    try:
        response = client.get("/api/v1/paper/proposals", params={"limit": limit})
        assert response.status_code == 200
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)
    return counts


def test_list_proposals_batched_query_count_is_independent_of_page_size(
    api_dsn: str,
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_trader: tuple[str, str, str],
) -> None:
    user_id, username, password = seeded_trader
    seeded_proposal_ids: list[str] = []
    try:
        for i in range(10):
            proposal_id = _new_uuid()
            _insert_bare_proposal(
                owner_connection,
                proposal_id=proposal_id,
                instrument_id=seeded_instrument_id,
                created_by=user_id,
                client_request_id=f"itest-nplus1-{i}-{_new_uuid()}",
            )
            seeded_proposal_ids.append(proposal_id)

        client_a, engine_a = _build_client_with_engine(api_dsn)
        try:
            _login(client_a, username=username, password=password)
            counts_at_10 = _count_ledger_batch_queries(client_a, engine_a, limit=50)
        finally:
            asyncio.run(engine_a.dispose())

        for i in range(40):
            proposal_id = _new_uuid()
            _insert_bare_proposal(
                owner_connection,
                proposal_id=proposal_id,
                instrument_id=seeded_instrument_id,
                created_by=user_id,
                client_request_id=f"itest-nplus1-more-{i}-{_new_uuid()}",
            )
            seeded_proposal_ids.append(proposal_id)

        client_b, engine_b = _build_client_with_engine(api_dsn)
        try:
            _login(client_b, username=username, password=password)
            counts_at_50 = _count_ledger_batch_queries(client_b, engine_b, limit=50)
        finally:
            asyncio.run(engine_b.dispose())

        # Exactly one risk_decisions query and one orders query either
        # way - not one per proposal. No order exists for any seeded
        # proposal, so `orders_by_proposal` is empty and the fills batch
        # read is skipped entirely (its own empty-input guard) rather than
        # issuing a query with an empty IN (): 0 either way, still O(1).
        assert counts_at_10 == counts_at_50 == {"risk_decisions": 1, "orders": 1, "fills": 0}
    finally:
        with owner_connection.cursor() as cur:
            cur.execute(
                "DELETE FROM paper.trade_proposals WHERE proposal_id = ANY(%s)",
                (seeded_proposal_ids,),
            )
        owner_connection.commit()
