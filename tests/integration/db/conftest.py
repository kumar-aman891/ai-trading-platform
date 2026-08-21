"""Shared fixtures for the Step 5 database/Redis security suite
(tests/integration/db/).

Every fixture here requires a live PostgreSQL/Redis instance bootstrapped by
ops/sql/roles_and_schemas.sql.tmpl - see docker-compose.test.yml and
ops/docker/test.env.example.

Two modes, controlled by ATP_REQUIRE_INTEGRATION_STACK (Phase 1 Step 11):

- Unset (the default): when the required TEST_* environment variable is
  unset or the instance is unreachable, fixtures call `pytest.skip` (never
  raise, never fake a pass) so this suite stays safe to collect and run in
  any environment, including one with no Docker at all - which is the case
  in the environment this suite was authored in. This is the mode a plain
  local `pytest` run uses.
- `ATP_REQUIRE_INTEGRATION_STACK=1`: the caller (docker-compose.test.yml's
  `test-runner` service, via ops/scripts/run_integration_tests.sh, or CI)
  is asserting the stack must be present. A missing env var or an
  unreachable instance calls `pytest.fail` instead of `pytest.skip`, so a
  broken or unreachable stack is reported as a failure rather than as 77
  quiet skips - see tests/unit/infra/test_integration_stack_gate.py, which
  proves this switch actually works without needing Docker itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSISTENCE_DIR = _REPO_ROOT / "persistence"

DSN_ENV_VARS = {
    "owner": "TEST_DATABASE_URL",
    "api": "TEST_ATP_API_DSN",
    "paper_exec": "TEST_ATP_PAPER_EXEC_DSN",
    "worker": "TEST_ATP_WORKER_DSN",
    "strategy": "TEST_ATP_STRATEGY_DSN",
}
REDIS_URL_ENV_VAR = "TEST_REDIS_URL"

CONNECT_TIMEOUT_SECONDS = 3

REQUIRE_STACK_ENV_VAR = "ATP_REQUIRE_INTEGRATION_STACK"


def _require_stack() -> bool:
    """Re-read on every call (not module-import-time) so tests can toggle
    the env var with monkeypatch without needing a reload."""
    return os.environ.get(REQUIRE_STACK_ENV_VAR) == "1"


def _require_dsn(env_var: str) -> str:
    dsn = os.environ.get(env_var)
    if not dsn:
        message = (
            f"{env_var} is not set - the Step 5 database security suite requires "
            f"the ephemeral stack in docker-compose.test.yml (see "
            f"ops/docker/test.env.example)."
        )
        if _require_stack():
            pytest.fail(f"{message} {REQUIRE_STACK_ENV_VAR}=1 requires the stack to be present.")
        pytest.skip(f"{message} Blocked in this environment, not failing.")
    return dsn


def _connect(dsn: str, *, label: str) -> psycopg.Connection:
    try:
        return psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except psycopg.OperationalError as exc:
        if _require_stack():
            pytest.fail(
                f"Could not connect as {label}: {exc} "
                f"({REQUIRE_STACK_ENV_VAR}=1 requires the stack to be reachable)."
            )
        pytest.skip(f"Could not connect as {label}: {exc}")


@pytest.fixture(scope="session")
def owner_dsn() -> str:
    return _require_dsn(DSN_ENV_VARS["owner"])


@pytest.fixture(scope="session")
def api_dsn() -> str:
    return _require_dsn(DSN_ENV_VARS["api"])


@pytest.fixture(scope="session")
def paper_exec_dsn() -> str:
    return _require_dsn(DSN_ENV_VARS["paper_exec"])


@pytest.fixture(scope="session")
def worker_dsn() -> str:
    return _require_dsn(DSN_ENV_VARS["worker"])


@pytest.fixture(scope="session")
def strategy_dsn() -> str:
    return _require_dsn(DSN_ENV_VARS["strategy"])


@pytest.fixture(scope="session")
def redis_url() -> str:
    return _require_dsn(REDIS_URL_ENV_VAR)


@pytest.fixture(scope="session")
def _owner_connection_session(owner_dsn: str) -> Iterator[psycopg.Connection]:
    conn = _connect(owner_dsn, label="atp_owner")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def owner_connection(_owner_connection_session: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """The session-scoped `atp_owner` connection, rolled back after every
    test.

    The connection itself stays session-scoped (connecting per-test is
    needlessly slow), but this per-test wrapper guarantees it is never
    handed to the next test still in PostgreSQL's `INERROR` state.
    Without it, a single test that leaves an aborted transaction open -
    e.g. by triggering a constraint or an append-only trigger without
    rolling back - poisons every later test sharing the connection, which
    then all fail with `InFailedSqlTransaction` regardless of their own
    correctness. The first real run against Postgres (Phase 1 Step 12
    Phase A) hit exactly that: one genuine bug produced 36 failures and
    23 errors, and the real cause was buried under cascade noise. Tests
    that own their own state still commit/roll back explicitly; this is a
    backstop, not a substitute for that."""
    yield _owner_connection_session
    _owner_connection_session.rollback()


# Reverse FK dependency order for everything a test-seeded `core.users` row
# can end up owning, via `paper.trade_proposals.created_by`:
#   cash_ledger -> fills -> orders -> order_intents -> risk_decisions
#   -> trade_proposals -> users
# (see atp_persistence.models.paper). A fixture that deletes only the
# `core.users` row raises `ForeignKeyViolation` the moment its test
# actually created a proposal - which is every test that exercises the
# execution path. Surfaced by the first real run against Postgres
# (Phase 1 Step 12 Phase A); before that these tests never reached
# teardown cleanly enough for it to fire.
_DELETE_USER_CASCADE_STATEMENTS = (
    """
    DELETE FROM paper.cash_ledger WHERE related_fill_id IN (
        SELECT f.fill_id FROM paper.fills f
        JOIN paper.orders o ON o.internal_order_id = f.internal_order_id
        JOIN paper.trade_proposals tp ON tp.proposal_id = o.proposal_id
        WHERE tp.created_by = %s)
    """,
    """
    DELETE FROM paper.fills WHERE internal_order_id IN (
        SELECT o.internal_order_id FROM paper.orders o
        JOIN paper.trade_proposals tp ON tp.proposal_id = o.proposal_id
        WHERE tp.created_by = %s)
    """,
    """
    DELETE FROM paper.orders WHERE proposal_id IN (
        SELECT proposal_id FROM paper.trade_proposals WHERE created_by = %s)
    """,
    """
    DELETE FROM paper.order_intents WHERE decision_id IN (
        SELECT rd.decision_id FROM paper.risk_decisions rd
        JOIN paper.trade_proposals tp ON tp.proposal_id = rd.proposal_id
        WHERE tp.created_by = %s)
    """,
    """
    DELETE FROM paper.risk_decisions WHERE proposal_id IN (
        SELECT proposal_id FROM paper.trade_proposals WHERE created_by = %s)
    """,
    "DELETE FROM paper.trade_proposals WHERE created_by = %s",
    # core.risk_config.created_by also references core.users. Only rows
    # this user authored are removed; the migration-seeded bootstrap
    # config has `created_by IS NULL` and so is never matched. The
    # `risk_config_immutable` trigger fires BEFORE UPDATE only, so DELETE
    # is permitted here.
    "DELETE FROM core.risk_config WHERE created_by = %s",
    "DELETE FROM core.sessions WHERE user_id = %s",
    "DELETE FROM core.users WHERE user_id = %s",
)


def delete_user_cascade(conn: psycopg.Connection, user_id: str) -> None:
    """Remove a test-seeded `core.users` row and everything referencing it.

    `audit.audit_events` is deliberately not touched - it is append-only
    (ADR-010) and its rejecting trigger refuses DELETE even for
    `atp_owner`. Audit rows carry no FK to `core.users` (`actor_id` is a
    plain Text column), so they never block this cleanup.

    Rolls back first: this runs as fixture teardown on the *session-scoped*
    `atp_owner` connection, and a test body that failed mid-transaction leaves
    that connection in PostgreSQL's `INERROR` state. Without the rollback every
    statement below raises `InFailedSqlTransaction`, the cleanup is abandoned,
    and the `core.users` row leaks permanently - surfacing much later as an
    unrelated failure in
    `test_risk_config_bootstrap.py::test_no_seeded_core_users_row_exists`
    rather than anywhere near the test that actually broke. Teardown must not
    inherit the body's aborted transaction. This hides nothing: the original
    failure is still reported as that test's own failure."""
    conn.rollback()
    with conn.cursor() as cur:
        for statement in _DELETE_USER_CASCADE_STATEMENTS:
            cur.execute(statement, (user_id,))
    conn.commit()


#: The four `core.kill_switch_state` rows seeded by migration 0001
#: (`_KILL_SWITCH_SEED_ROWS`). These are the only switches 0001's
#: `downgrade()` removes *by predicate* (`DELETE FROM core.kill_switch_state
#: WHERE switch_id = ANY(...)`, before the append-only trigger is dropped and
#: before any table is dropped); every other switch row is removed only by the
#: `DROP TABLE` at the end of that same downgrade.
#:
#: That asymmetry is why no test may write `core.kill_switch_history` against
#: one of these ids. History is append-only (ADR-007) and its rejecting trigger
#: refuses DELETE even for `atp_owner`, so such a row is permanent for the
#: lifetime of the database - and it holds an FK to the seed row, making that
#: predicate DELETE fail with `ForeignKeyViolation` from then on. `alembic
#: downgrade base` is broken for the rest of the session as a result. Use
#: `existing_test_switch` / `new_test_switch_id()` below instead.
MIGRATION_SEEDED_SWITCH_IDS = ("API_EXECUTION", "GLOBAL_LIVE", "LIVE_ACCOUNT", "PAPER")


def new_test_switch_id() -> str:
    """A unique, non-migration-seeded `switch_id` safe to write history for."""
    return f"STRATEGY:it-{uuid.uuid4()}"


@pytest.fixture
def existing_test_switch(owner_connection: psycopg.Connection) -> Iterator[str]:
    """A disengaged, non-seeded `core.kill_switch_state` row that already
    exists when the test starts.

    For exercising any "the row is already there" path (e.g.
    `apply_transition`'s UPDATE branch) without coupling the test to a
    migration-seeded switch. What selects that branch is the row *existing*,
    not the row being seeded, so coverage is identical and the test stops
    mutating state the migration owns.
    """
    switch_id = new_test_switch_id()
    with owner_connection.cursor() as cur:
        cur.execute(
            "INSERT INTO core.kill_switch_state (switch_id, engaged, updated_at, "
            "updated_by, reason) VALUES (%s, false, now(), NULL, NULL)",
            (switch_id,),
        )
    owner_connection.commit()

    yield switch_id

    # Teardown must never attempt a delete the database is designed to reject.
    # If the test appended history for this switch, that history is append-only
    # and permanent, and the FK makes the parent row equally permanent - so
    # delete the state row only when nothing references it. A row left behind
    # here is harmless: it is not migration-seeded, so `downgrade base` removes
    # it by `DROP TABLE` rather than by predicate.
    owner_connection.rollback()
    with owner_connection.cursor() as cur:
        cur.execute(
            "DELETE FROM core.kill_switch_state WHERE switch_id = %s AND NOT EXISTS "
            "(SELECT 1 FROM core.kill_switch_history h WHERE h.switch_id = %s)",
            (switch_id, switch_id),
        )
    owner_connection.commit()


@pytest.fixture(scope="session")
def api_connection(api_dsn: str) -> Iterator[psycopg.Connection]:
    conn = _connect(api_dsn, label="atp_api")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def paper_exec_connection(paper_exec_dsn: str) -> Iterator[psycopg.Connection]:
    conn = _connect(paper_exec_dsn, label="atp_paper_exec")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def worker_connection(worker_dsn: str) -> Iterator[psycopg.Connection]:
    conn = _connect(worker_dsn, label="atp_worker")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def strategy_connection(strategy_dsn: str) -> Iterator[psycopg.Connection]:
    conn = _connect(strategy_dsn, label="atp_strategy")
    try:
        yield conn
    finally:
        conn.close()


def _as_sync_psycopg_url(dsn: str) -> str:
    """`TEST_DATABASE_URL` uses the bare `postgresql://` scheme (see
    ops/docker/test.env.example); SQLAlchemy needs the driver named
    explicitly to pick psycopg3 (the only Postgres driver this workspace
    installs - psycopg2 is not a dependency anywhere)."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


@pytest.fixture(scope="session")
def migrated_database(owner_dsn: str) -> str:
    """Applies the Phase 1 Step 6 Alembic chain (`alembic upgrade head`)
    once per test session, as `atp_owner` - the only role with DDL
    privileges (ops/sql/roles_and_schemas.sql.tmpl). Returns the
    SQLAlchemy-flavoured DSN used, for tests that want to build their own
    engine against the now-migrated database.

    Runs `alembic` as a subprocess (not `alembic.command.upgrade()` in
    -process) so this fixture exercises the exact same command path
    `ops/scripts/run_integration_tests.sh`/a real deployment would use, not
    a shortcut that could pass while the CLI invocation itself is broken.
    """
    sync_url = _as_sync_psycopg_url(owner_dsn)
    env = os.environ.copy()
    env["ATP_MIGRATION_DATABASE_URL"] = sync_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=_PERSISTENCE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic upgrade head failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return sync_url
