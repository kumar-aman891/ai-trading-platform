"""Phase 1 Step 6: database-level integrity, proven against a real
PostgreSQL instance - CHECK constraints, UNIQUE constraints, and foreign
keys reject bad data even from a caller that bypasses every domain-level
validation (docs/schemas/*.md's "Constraints" sections).

Every test connects as `atp_owner` and works entirely through raw SQL, not
the ORM - the point is to prove the *database* rejects corruption from a
non-domain caller (e.g. a stray script, a bug in a future adapter), not
merely that Python's dataclasses do.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from tests.integration.db.conftest import delete_user_cascade


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _random_version() -> int:
    """A version number vanishingly unlikely to collide with another test
    in the same run - `core.risk_config` has `UNIQUE (mode, version)`.

    Three bytes, not four: `core.risk_config.version` is a Postgres
    `integer` (max 2147483647) and `os.urandom(4)` reaches 4294967295, so
    roughly half of all draws raised `NumericValueOutOfRange`. Surfaced by
    the first real run against Postgres (Phase 1 Step 12 Phase A) - see
    the same fix in test_audit_immutability.py and test_repositories.py.
    16777216 values is still far more than enough for one test session."""
    return int.from_bytes(os.urandom(3), "big")


@pytest.fixture
def seeded_instrument_id(migrated_database: str, owner_connection: psycopg.Connection) -> str:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT instrument_id FROM core.instruments WHERE provider = 'FIXTURE' LIMIT 1")
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None, "expected at least one seeded FIXTURE instrument"
    # `str(...)`: psycopg3 maps a Postgres `uuid` column to a Python
    # `UUID` object, but every domain identifier is `NewType("...", str)`
    # and `uuid_pk` uses `as_uuid=False`, so SQLAlchemy hands the app a
    # plain str. Returning the raw UUID made round-trip assertions compare
    # UUID('...') against '...' and fail (Phase 1 Step 12 Phase A).
    return str(row[0])


@pytest.fixture
def seeded_user_id(migrated_database: str, owner_connection: psycopg.Connection) -> Iterator[str]:
    """core.users has no seed data (user.md forbids it), so tests that need
    a valid `created_by` FK target insert - and clean up - their own row."""
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


def test_trade_proposal_rejects_zero_quantity(
    owner_connection: psycopg.Connection, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type,
                     product, client_request_id, expected_risk, created_by, created_at)
                VALUES (%s, 'PAPER', %s, 'BUY', 0, 'MARKET', 'CNC', %s, '{}', %s, now())
                """,
            (_new_uuid(), seeded_instrument_id, _new_uuid(), seeded_user_id),
        )
    owner_connection.rollback()


def test_trade_proposal_rejects_limit_order_without_limit_price(
    owner_connection: psycopg.Connection, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type,
                     product, client_request_id, expected_risk, created_by, created_at)
                VALUES (%s, 'PAPER', %s, 'BUY', 1, 'LIMIT', 'CNC', %s, '{}', %s, now())
                """,
            (_new_uuid(), seeded_instrument_id, _new_uuid(), seeded_user_id),
        )
    owner_connection.rollback()


def test_trade_proposal_rejects_wrong_mode(
    owner_connection: psycopg.Connection, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type,
                     product, client_request_id, expected_risk, created_by, created_at)
                VALUES (%s, 'LIVE', %s, 'BUY', 1, 'MARKET', 'CNC', %s, '{}', %s, now())
                """,
            (_new_uuid(), seeded_instrument_id, _new_uuid(), seeded_user_id),
        )
    owner_connection.rollback()


def test_trade_proposal_rejects_duplicate_client_request_id(
    owner_connection: psycopg.Connection, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    client_request_id = _new_uuid()
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper.trade_proposals
                (proposal_id, mode, instrument_id, side, quantity, order_type,
                 product, client_request_id, expected_risk, created_by, created_at)
            VALUES (%s, 'PAPER', %s, 'BUY', 1, 'MARKET', 'CNC', %s, '{}', %s, now())
            """,
            (_new_uuid(), seeded_instrument_id, client_request_id, seeded_user_id),
        )
    owner_connection.commit()

    with pytest.raises(psycopg.errors.UniqueViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type,
                     product, client_request_id, expected_risk, created_by, created_at)
                VALUES (%s, 'PAPER', %s, 'SELL', 1, 'MARKET', 'CNC', %s, '{}', %s, now())
                """,
            (_new_uuid(), seeded_instrument_id, client_request_id, seeded_user_id),
        )
    owner_connection.rollback()


def test_trade_proposal_rejects_unknown_instrument(
    owner_connection: psycopg.Connection, seeded_user_id: str
) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type,
                     product, client_request_id, expected_risk, created_by, created_at)
                VALUES (%s, 'PAPER', %s, 'BUY', 1, 'MARKET', 'CNC', %s, '{}', %s, now())
                """,
            (_new_uuid(), _new_uuid(), _new_uuid(), seeded_user_id),
        )
    owner_connection.rollback()


def test_trade_proposal_accepts_human_authored_row_with_null_strategy_id(
    owner_connection: psycopg.Connection, seeded_instrument_id: str, seeded_user_id: str
) -> None:
    """ADR-015: `created_by` non-null, `strategy_id` null - the existing
    human-submission shape - still satisfies `proposal_has_an_author`."""
    with owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type,
                     product, client_request_id, expected_risk, created_by, strategy_id, created_at)
                VALUES (%s, 'PAPER', %s, 'BUY', 1, 'MARKET', 'CNC', %s, '{}', %s, NULL, now())
                """,
            (_new_uuid(), seeded_instrument_id, _new_uuid(), seeded_user_id),
        )
    owner_connection.commit()


def test_trade_proposal_accepts_strategy_authored_row_with_null_created_by(
    owner_connection: psycopg.Connection, seeded_instrument_id: str
) -> None:
    """ADR-015: `created_by` null, `strategy_id` non-null - the new
    strategy-submission shape - satisfies `proposal_has_an_author` with no
    `core.users` row involved at all.

    The row is deleted explicitly rather than left for `delete_user_cascade`:
    that helper is keyed on `created_by`, so once ADR-015 made the column
    nullable it became structurally unable to reach a strategy-authored
    row. A leaked `created_by IS NULL` row is not harmless - it makes every
    later `alembic downgrade` past 0006 fail at
    `ALTER COLUMN created_by SET NOT NULL` (found by CI, Milestone 2D)."""
    proposal_id = _new_uuid()
    try:
        with owner_connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type,
                     product, client_request_id, expected_risk, created_by, strategy_id,
                     strategy_version, created_at)
                VALUES (%s, 'PAPER', %s, 'BUY', 1, 'MARKET', 'CNC', %s, '{}', NULL, %s, 1, now())
                """,
                (proposal_id, seeded_instrument_id, _new_uuid(), _new_uuid()),
            )
        owner_connection.commit()
    finally:
        with owner_connection.cursor() as cur:
            cur.execute("DELETE FROM paper.trade_proposals WHERE proposal_id = %s", (proposal_id,))
        owner_connection.commit()


def test_trade_proposal_rejects_row_with_neither_created_by_nor_strategy_id(
    owner_connection: psycopg.Connection, seeded_instrument_id: str
) -> None:
    """ADR-015: an unattributable proposal - both `created_by` and
    `strategy_id` null - violates `proposal_has_an_author`."""
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.trade_proposals
                    (proposal_id, mode, instrument_id, side, quantity, order_type,
                     product, client_request_id, expected_risk, created_by, strategy_id, created_at)
                VALUES (%s, 'PAPER', %s, 'BUY', 1, 'MARKET', 'CNC', %s, '{}', NULL, NULL, now())
                """,
            (_new_uuid(), seeded_instrument_id, _new_uuid()),
        )
    owner_connection.rollback()


def test_fill_rejects_nonpositive_quantity(
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_user_id: str,
) -> None:
    order_id = _prepare_order(owner_connection, seeded_instrument_id, seeded_user_id)
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.fills
                    (fill_id, mode, internal_order_id, quantity, price, fees, taxes,
                     simulated, source, filled_at)
                VALUES (%s, 'PAPER', %s, 0, 100, 0, 0, true, 'PAPER_SIMULATOR', now())
                """,
            (_new_uuid(), order_id),
        )
    owner_connection.rollback()


def test_fill_rejects_negative_fees(
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_user_id: str,
) -> None:
    order_id = _prepare_order(owner_connection, seeded_instrument_id, seeded_user_id)
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                INSERT INTO paper.fills
                    (fill_id, mode, internal_order_id, quantity, price, fees, taxes,
                     simulated, source, filled_at)
                VALUES (%s, 'PAPER', %s, 1, 100, -1, 0, true, 'PAPER_SIMULATOR', now())
                """,
            (_new_uuid(), order_id),
        )
    owner_connection.rollback()


def test_order_intent_is_single_use_per_decision(
    owner_connection: psycopg.Connection,
    seeded_instrument_id: str,
    seeded_user_id: str,
) -> None:
    decision_id = _prepare_decision(owner_connection, seeded_instrument_id, seeded_user_id)
    with owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper.order_intents
                (intent_id, mode, decision_id, proposal_id, canonical_payload,
                 payload_hash, minted_at, expires_at)
            SELECT %s, 'PAPER', decision_id, proposal_id, '{}', 'hash1', now(), now() + interval '30 seconds'
            FROM paper.risk_decisions WHERE decision_id = %s
            """,
            (_new_uuid(), decision_id),
        )
    owner_connection.commit()

    with pytest.raises(psycopg.errors.UniqueViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper.order_intents
                (intent_id, mode, decision_id, proposal_id, canonical_payload,
                 payload_hash, minted_at, expires_at)
            SELECT %s, 'PAPER', decision_id, proposal_id, '{}', 'hash2', now(), now() + interval '30 seconds'
            FROM paper.risk_decisions WHERE decision_id = %s
            """,
            (_new_uuid(), decision_id),
        )
    owner_connection.rollback()


def test_kill_switch_state_requires_reason_when_updated_by_is_set(
    migrated_database: str, owner_connection: psycopg.Connection, seeded_user_id: str
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
                UPDATE core.kill_switch_state
                SET updated_by = %s, updated_at = now(), reason = NULL
                WHERE switch_id = 'PAPER'
                """,
            (seeded_user_id,),
        )
    owner_connection.rollback()


def _prepare_order(conn: psycopg.Connection, instrument_id: str, user_id: str) -> str:
    decision_id = _prepare_decision(conn, instrument_id, user_id)
    intent_id = _new_uuid()
    order_id = _new_uuid()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper.order_intents
                (intent_id, mode, decision_id, proposal_id, canonical_payload,
                 payload_hash, minted_at, expires_at)
            SELECT %s, 'PAPER', decision_id, proposal_id, '{}', 'hash', now(), now() + interval '30 seconds'
            FROM paper.risk_decisions WHERE decision_id = %s
            """,
            (intent_id, decision_id),
        )
        cur.execute(
            """
            INSERT INTO paper.orders
                (internal_order_id, mode, proposal_id, intent_id, idempotency_key,
                 status, submitted_at, last_update_at)
            SELECT %s, 'PAPER', proposal_id, %s, %s, 'SUBMITTED', now(), now()
            FROM paper.risk_decisions WHERE decision_id = %s
            """,
            (order_id, intent_id, _new_uuid(), decision_id),
        )
    conn.commit()
    return order_id


def _prepare_decision(conn: psycopg.Connection, instrument_id: str, user_id: str) -> str:
    proposal_id = _new_uuid()
    risk_config_id = _new_uuid()
    decision_id = _new_uuid()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO paper.trade_proposals
                (proposal_id, mode, instrument_id, side, quantity, order_type,
                 product, client_request_id, expected_risk, created_by, created_at)
            VALUES (%s, 'PAPER', %s, 'BUY', 1, 'MARKET', 'CNC', %s, '{}', %s, now())
            """,
            (proposal_id, instrument_id, _new_uuid(), user_id),
        )
        cur.execute(
            """
            INSERT INTO core.risk_config
                (risk_config_id, mode, version, config, config_hash, active, created_at, created_by)
            VALUES (%s, 'PAPER', %s, '{}', %s, false, now(), %s)
            """,
            (risk_config_id, _random_version(), _new_uuid(), user_id),
        )
        cur.execute(
            """
            INSERT INTO paper.risk_decisions
                (decision_id, mode, proposal_id, outcome, rule_results,
                 risk_config_id, limit_snapshot_hash, decided_at)
            VALUES (%s, 'PAPER', %s, 'APPROVED',
                    '[{"rule_id":"X","outcome":"PASS","message":"ok","evidence":{}}]',
                    %s, 'hash', now())
            """,
            (decision_id, proposal_id, risk_config_id),
        )
    conn.commit()
    return decision_id


# --- core.job_queue (migration 0005_job_queue_claim_constraints, ADR-013) ---


def _insert_job(
    conn: psycopg.Connection,
    *,
    job_id: str,
    job_type: str,
    status: str = "PENDING",
    attempts: int = 0,
    max_attempts: int = 3,
    completed_at_set: bool = False,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.job_queue
                (job_id, job_type, payload, status, attempts, max_attempts,
                 scheduled_for, completed_at, created_at)
            VALUES
                (%s, %s, '{}', %s, %s, %s, now(),
                 CASE WHEN %s THEN now() ELSE NULL END, now())
            """,
            (job_id, job_type, status, attempts, max_attempts, completed_at_set),
        )
    conn.commit()


def _delete_job(conn: psycopg.Connection, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM core.job_queue WHERE job_id = %s", (job_id,))
    conn.commit()


def test_job_queue_accepts_a_well_formed_pending_row(
    owner_connection: psycopg.Connection,
) -> None:
    """The baseline valid state (ADR-013 Section 3-4) must remain
    insertable - these constraints must reject only malformed rows,
    never legitimate ones."""
    job_id = _new_uuid()
    try:
        _insert_job(owner_connection, job_id=job_id, job_type="RETENTION")
    finally:
        _delete_job(owner_connection, job_id)


def test_job_queue_rejects_a_second_pending_job_of_the_same_type(
    owner_connection: psycopg.Connection,
) -> None:
    """ADR-013 Section 6: ux_job_queue_one_live_per_type - at most one
    PENDING-or-RUNNING row per job_type, enforced by the database so the
    scheduler's insert path is insert-then-catch, not check-then-insert."""
    job_id_a, job_id_b = _new_uuid(), _new_uuid()
    _insert_job(owner_connection, job_id=job_id_a, job_type="SESSION_REAP")
    try:
        with pytest.raises(psycopg.errors.UniqueViolation), owner_connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO core.job_queue
                    (job_id, job_type, payload, status, attempts, max_attempts,
                     scheduled_for, created_at)
                VALUES (%s, 'SESSION_REAP', '{}', 'PENDING', 0, 3, now(), now())
                """,
                (job_id_b,),
            )
        owner_connection.rollback()
    finally:
        _delete_job(owner_connection, job_id_a)


def test_job_queue_rejects_a_running_job_colliding_with_a_pending_one_of_the_same_type(
    owner_connection: psycopg.Connection,
) -> None:
    """The partial index covers PENDING and RUNNING together - a RUNNING
    row is still "live" and must not coexist with a PENDING row of the
    same job_type either."""
    job_id_a, job_id_b = _new_uuid(), _new_uuid()
    _insert_job(
        owner_connection, job_id=job_id_a, job_type="AUDIT_INTEGRITY_CHECK", status="RUNNING"
    )
    try:
        with pytest.raises(psycopg.errors.UniqueViolation), owner_connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO core.job_queue
                    (job_id, job_type, payload, status, attempts, max_attempts,
                     scheduled_for, created_at)
                VALUES (%s, 'AUDIT_INTEGRITY_CHECK', '{}', 'PENDING', 0, 3, now(), now())
                """,
                (job_id_b,),
            )
        owner_connection.rollback()
    finally:
        _delete_job(owner_connection, job_id_a)


def test_job_queue_allows_one_live_job_per_distinct_type_simultaneously(
    owner_connection: psycopg.Connection,
) -> None:
    """The uniqueness is scoped to job_type, not table-wide - three live
    jobs of three different types coexist without conflict."""
    job_ids = [_new_uuid(), _new_uuid(), _new_uuid()]
    job_types = ["SESSION_REAP", "AUDIT_INTEGRITY_CHECK", "RETENTION"]
    try:
        for job_id, job_type in zip(job_ids, job_types, strict=True):
            _insert_job(owner_connection, job_id=job_id, job_type=job_type)
    finally:
        for job_id in job_ids:
            _delete_job(owner_connection, job_id)


def test_job_queue_allows_a_new_pending_job_once_the_prior_one_is_terminal(
    owner_connection: psycopg.Connection,
) -> None:
    """The partial index only covers PENDING/RUNNING - once a job reaches
    a terminal state, a fresh recurring job of the same type may be
    scheduled (ADR-013 Section 6's recurring-singleton model)."""
    job_id_a, job_id_b = _new_uuid(), _new_uuid()
    _insert_job(owner_connection, job_id=job_id_a, job_type="RETENTION")
    try:
        with owner_connection.cursor() as cur:
            cur.execute(
                "UPDATE core.job_queue SET status = 'SUCCEEDED', completed_at = now() "
                "WHERE job_id = %s",
                (job_id_a,),
            )
        owner_connection.commit()

        _insert_job(owner_connection, job_id=job_id_b, job_type="RETENTION")
    finally:
        _delete_job(owner_connection, job_id_a)
        _delete_job(owner_connection, job_id_b)


@pytest.mark.parametrize("status", ["PENDING", "RUNNING"])
def test_job_queue_rejects_a_non_terminal_row_with_completed_at_set(
    owner_connection: psycopg.Connection, status: str
) -> None:
    """ADR-013 Section 3: completed_at must be NULL for PENDING/RUNNING
    rows - ck_job_queue_terminal_state_has_completed_at."""
    job_id = _new_uuid()
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.job_queue
                (job_id, job_type, payload, status, attempts, max_attempts,
                 scheduled_for, completed_at, created_at)
            VALUES (%s, 'RETENTION', '{}', %s, 0, 3, now(), now(), now())
            """,
            (job_id, status),
        )
    owner_connection.rollback()


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
def test_job_queue_rejects_a_terminal_row_without_completed_at(
    owner_connection: psycopg.Connection, status: str
) -> None:
    """ADR-013 Section 3: a terminal row (SUCCEEDED/FAILED) must always
    carry a completed_at - ck_job_queue_terminal_state_has_completed_at."""
    job_id = _new_uuid()
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.job_queue
                (job_id, job_type, payload, status, attempts, max_attempts,
                 scheduled_for, created_at)
            VALUES (%s, 'RETENTION', '{}', %s, 1, 3, now(), now())
            """,
            (job_id, status),
        )
    owner_connection.rollback()


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
def test_job_queue_accepts_a_terminal_row_with_completed_at(
    owner_connection: psycopg.Connection, status: str
) -> None:
    """The valid terminal state must remain insertable."""
    job_id = _new_uuid()
    try:
        _insert_job(
            owner_connection,
            job_id=job_id,
            job_type="RETENTION",
            status=status,
            attempts=1,
            completed_at_set=True,
        )
    finally:
        _delete_job(owner_connection, job_id)


def test_job_queue_rejects_negative_attempts(owner_connection: psycopg.Connection) -> None:
    """ADR-013 Section 4: attempts_within_bounds - attempts >= 0."""
    job_id = _new_uuid()
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.job_queue
                (job_id, job_type, payload, status, attempts, max_attempts,
                 scheduled_for, created_at)
            VALUES (%s, 'RETENTION', '{}', 'PENDING', -1, 3, now(), now())
            """,
            (job_id,),
        )
    owner_connection.rollback()


def test_job_queue_rejects_attempts_exceeding_max_attempts(
    owner_connection: psycopg.Connection,
) -> None:
    """ADR-013 Section 4: attempts can never exceed max_attempts - the
    claim protocol increments attempts at claim time (Tx A) and stops
    re-claiming once exhaustion is reached (Tx C moves the row to
    FAILED), so a row with attempts > max_attempts can only mean a bug
    or direct tampering, not a legitimate worker state."""
    job_id = _new_uuid()
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.job_queue
                (job_id, job_type, payload, status, attempts, max_attempts,
                 scheduled_for, created_at)
            VALUES (%s, 'RETENTION', '{}', 'PENDING', 4, 3, now(), now())
            """,
            (job_id,),
        )
    owner_connection.rollback()


def test_job_queue_rejects_max_attempts_of_zero(owner_connection: psycopg.Connection) -> None:
    """ADR-013 Section 4: max_attempts >= 1 - a row with max_attempts = 0
    could never be legitimately claimed at all (attempts increments at
    claim, so the first claim would immediately violate
    attempts <= max_attempts)."""
    job_id = _new_uuid()
    with pytest.raises(psycopg.errors.CheckViolation), owner_connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.job_queue
                (job_id, job_type, payload, status, attempts, max_attempts,
                 scheduled_for, created_at)
            VALUES (%s, 'RETENTION', '{}', 'PENDING', 0, 0, now(), now())
            """,
            (job_id,),
        )
    owner_connection.rollback()


def test_job_queue_accepts_attempts_equal_to_max_attempts(
    owner_connection: psycopg.Connection,
) -> None:
    """The boundary itself (attempts == max_attempts) is valid - it is
    the state of a job on its final permitted attempt."""
    job_id = _new_uuid()
    try:
        _insert_job(
            owner_connection,
            job_id=job_id,
            job_type="RETENTION",
            status="FAILED",
            attempts=3,
            max_attempts=3,
            completed_at_set=True,
        )
    finally:
        _delete_job(owner_connection, job_id)
