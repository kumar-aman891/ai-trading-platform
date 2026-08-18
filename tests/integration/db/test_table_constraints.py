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


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _random_version() -> int:
    """A version number vanishingly unlikely to collide with another test
    in the same run - `core.risk_config` has `UNIQUE (mode, version)`."""
    return int.from_bytes(os.urandom(4), "big")


@pytest.fixture
def seeded_instrument_id(migrated_database: str, owner_connection: psycopg.Connection) -> str:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT instrument_id FROM core.instruments WHERE provider = 'FIXTURE' LIMIT 1")
        row = cur.fetchone()
    owner_connection.rollback()
    assert row is not None, "expected at least one seeded FIXTURE instrument"
    return row[0]


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
    with owner_connection.cursor() as cur:
        cur.execute("DELETE FROM core.users WHERE user_id = %s", (user_id,))
    owner_connection.commit()


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
