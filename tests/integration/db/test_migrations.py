"""Phase 1 Step 6: migration upgrade/downgrade against a real database.

Self-contained (does not depend on the shared `migrated_database` fixture)
because this module exercises `downgrade` itself and always leaves the
database back at `head` when it finishes, regardless of test order, so
other modules that assume a migrated database are never left stranded on a
partial schema.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg

_PERSISTENCE_DIR = Path(__file__).resolve().parents[3] / "persistence"


def _as_sync_psycopg_url(dsn: str) -> str:
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


_EXPECTED_TABLES = {
    ("core", "users"),
    ("core", "sessions"),
    ("core", "instruments"),
    ("core", "risk_config"),
    ("core", "kill_switch_state"),
    ("core", "kill_switch_history"),
    ("core", "job_queue"),
    ("audit", "audit_events"),
    ("paper", "trade_proposals"),
    ("paper", "risk_decisions"),
    ("paper", "order_intents"),
    ("paper", "orders"),
    ("paper", "fills"),
    ("paper", "positions"),
    ("paper", "cash_ledger"),
}


def _run_alembic(*args: str, sync_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["ATP_MIGRATION_DATABASE_URL"] = sync_url
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PERSISTENCE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _existing_tables(conn: psycopg.Connection, schemas: set[str]) -> set[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema = ANY(%s)",
            (list(schemas),),
        )
        return {(row[0], row[1]) for row in cur.fetchall()}


def test_upgrade_head_creates_every_expected_table(
    owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    sync_url = _as_sync_psycopg_url(owner_dsn)
    result = _run_alembic("upgrade", "head", sync_url=sync_url)
    assert result.returncode == 0, result.stderr

    found = _existing_tables(owner_connection, {"core", "audit", "paper"})
    assert found >= _EXPECTED_TABLES


def test_live_schema_has_no_tables(owner_connection: psycopg.Connection) -> None:
    found = _existing_tables(owner_connection, {"live"})
    assert found == set()


def test_downgrade_base_then_upgrade_head_is_idempotent(owner_dsn: str) -> None:
    """Proves the downgrade path actually works (not merely declared) and
    that re-upgrading afterward restores the identical schema - the
    database is left at `head` either way, so this test cannot strand
    later tests on a half-migrated database."""
    sync_url = _as_sync_psycopg_url(owner_dsn)
    try:
        down_result = _run_alembic("downgrade", "base", sync_url=sync_url)
        assert down_result.returncode == 0, down_result.stderr

        with psycopg.connect(owner_dsn, connect_timeout=3) as conn:
            # core.alembic_version is Alembic's own version-tracking table
            # (env.py's version_table_schema="core"), not something any
            # migration's downgrade() creates or drops - it legitimately
            # persists (with zero rows) at `base`. First real run against
            # Postgres (Phase 1 Step 12 Phase A) found this assertion had
            # never actually accounted for it.
            assert _existing_tables(conn, {"core", "audit", "paper"}) == {
                ("core", "alembic_version")
            }
    finally:
        up_result = _run_alembic("upgrade", "head", sync_url=sync_url)
        assert up_result.returncode == 0, up_result.stderr

    with psycopg.connect(owner_dsn, connect_timeout=3) as conn:
        assert _existing_tables(conn, {"core", "audit", "paper"}) >= _EXPECTED_TABLES


def test_alembic_version_table_reports_head_after_upgrade(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT version_num FROM core.alembic_version")
        (version,) = cur.fetchone()  # type: ignore[misc]
        assert version == "0006_strategy_attribution"


_JOB_QUEUE_CONSTRAINTS = {
    "ux_job_queue_one_live_per_type",
    "ck_job_queue_terminal_state_has_completed_at",
    "ck_job_queue_attempts_within_bounds",
}


def _job_queue_constraint_names(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'core.job_queue'::regclass AND conname = ANY(%s)",
            (list(_JOB_QUEUE_CONSTRAINTS),),
        )
        check_names = {row[0] for row in cur.fetchall()}
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'core' AND tablename = 'job_queue' AND indexname = ANY(%s)",
            (list(_JOB_QUEUE_CONSTRAINTS),),
        )
        index_names = {row[0] for row in cur.fetchall()}
    return check_names | index_names


def test_downgrade_to_0004_then_upgrade_to_0005_restores_job_queue_constraints(
    owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    """Migration 0005_job_queue_claim_constraints (ADR-013) hand-writes
    DDL rather than deriving it from Base.metadata.sorted_tables (unlike
    migration 0001) - the only way to prove both directions of that DDL
    are actually correct, not merely declared, is to run them against a
    real database. Always leaves the database at head, regardless of
    outcome, so later tests are never stranded on 0004's schema."""
    sync_url = _as_sync_psycopg_url(owner_dsn)
    try:
        down_result = _run_alembic("downgrade", "0004_paper_cash_ledger_seed", sync_url=sync_url)
        assert down_result.returncode == 0, down_result.stderr

        with psycopg.connect(owner_dsn, connect_timeout=3) as conn:
            assert _job_queue_constraint_names(conn) == set()

        up_result = _run_alembic("upgrade", "head", sync_url=sync_url)
        assert up_result.returncode == 0, up_result.stderr
    finally:
        # Idempotent if the block above already succeeded; guarantees head
        # even if downgrade or the first upgrade attempt failed midway.
        final_result = _run_alembic("upgrade", "head", sync_url=sync_url)
        assert final_result.returncode == 0, final_result.stderr

    with owner_connection.cursor() as cur:
        cur.execute("SELECT version_num FROM core.alembic_version")
        (version,) = cur.fetchone()  # type: ignore[misc]
    assert version == "0005_job_queue_claim_constraints"
    assert _job_queue_constraint_names(owner_connection) == _JOB_QUEUE_CONSTRAINTS


def _trade_proposals_created_by_is_nullable(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'paper' AND table_name = 'trade_proposals' "
            "AND column_name = 'created_by'"
        )
        (is_nullable,) = cur.fetchone()  # type: ignore[misc]
    return is_nullable == "YES"


def _author_check_exists(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = "
            "'ck_trade_proposals_proposal_has_an_author' "
            "AND conrelid = 'paper.trade_proposals'::regclass"
        )
        return cur.fetchone() is not None


def test_downgrade_to_0005_then_upgrade_to_0006_restores_proposal_attribution(
    owner_dsn: str, owner_connection: psycopg.Connection
) -> None:
    """ADR-015 / migration 0006: proves both directions of the hand-written
    ALTER COLUMN / CHECK constraint DDL actually work against a real
    database, not merely declared - mirrors migration 0005's own up/down
    proof pattern. Always leaves the database at head, regardless of
    outcome, so later tests are never stranded on 0005's schema."""
    sync_url = _as_sync_psycopg_url(owner_dsn)
    try:
        down_result = _run_alembic(
            "downgrade", "0005_job_queue_claim_constraints", sync_url=sync_url
        )
        assert down_result.returncode == 0, down_result.stderr

        with psycopg.connect(owner_dsn, connect_timeout=3) as conn:
            assert _trade_proposals_created_by_is_nullable(conn) is False
            assert _author_check_exists(conn) is False

        up_result = _run_alembic("upgrade", "head", sync_url=sync_url)
        assert up_result.returncode == 0, up_result.stderr
    finally:
        final_result = _run_alembic("upgrade", "head", sync_url=sync_url)
        assert final_result.returncode == 0, final_result.stderr

    with owner_connection.cursor() as cur:
        cur.execute("SELECT version_num FROM core.alembic_version")
        (version,) = cur.fetchone()  # type: ignore[misc]
    assert version == "0006_strategy_attribution"
    assert _trade_proposals_created_by_is_nullable(owner_connection) is True
    assert _author_check_exists(owner_connection) is True


def test_kill_switch_state_seed_rows_present(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT switch_id, engaged FROM core.kill_switch_state ORDER BY switch_id")
        rows = dict(cur.fetchall())
    assert rows == {
        "API_EXECUTION": False,
        "GLOBAL_LIVE": True,
        "LIVE_ACCOUNT": True,
        "PAPER": False,
    }


def test_fixture_instruments_seeded(
    migrated_database: str, owner_connection: psycopg.Connection
) -> None:
    with owner_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM core.instruments WHERE provider = 'FIXTURE'")
        (count,) = cur.fetchone()  # type: ignore[misc]
    assert count >= 20
