"""Shared fixtures for the Step 5 database/Redis security suite
(tests/integration/db/).

Every fixture here requires a live PostgreSQL/Redis instance bootstrapped by
ops/sql/roles_and_schemas.sql.tmpl - see docker-compose.test.yml and
ops/docker/test.env.example. When the required TEST_* environment variable
is unset or the instance is unreachable, fixtures call `pytest.skip` (never
raise, never fake a pass) so this suite is safe to collect and run in any
environment, including one with no Docker at all - which is the case in the
environment this suite was authored in.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest

DSN_ENV_VARS = {
    "owner": "TEST_DATABASE_URL",
    "api": "TEST_ATP_API_DSN",
    "paper_exec": "TEST_ATP_PAPER_EXEC_DSN",
    "worker": "TEST_ATP_WORKER_DSN",
}
REDIS_URL_ENV_VAR = "TEST_REDIS_URL"

CONNECT_TIMEOUT_SECONDS = 3


def _require_dsn(env_var: str) -> str:
    dsn = os.environ.get(env_var)
    if not dsn:
        pytest.skip(
            f"{env_var} is not set - the Step 5 database security suite requires "
            f"the ephemeral stack in docker-compose.test.yml (see "
            f"ops/docker/test.env.example). Blocked in this environment, not failing."
        )
    return dsn


def _connect(dsn: str, *, label: str) -> psycopg.Connection:
    try:
        return psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    except psycopg.OperationalError as exc:
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
def owner_connection(owner_dsn: str) -> Iterator[psycopg.Connection]:
    conn = _connect(owner_dsn, label="atp_owner")
    try:
        yield conn
    finally:
        conn.close()


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
