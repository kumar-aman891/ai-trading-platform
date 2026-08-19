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
