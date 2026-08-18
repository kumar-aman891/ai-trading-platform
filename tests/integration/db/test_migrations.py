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
            assert _existing_tables(conn, {"core", "audit", "paper"}) == set()
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
        assert version == "0003_table_grants"


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
