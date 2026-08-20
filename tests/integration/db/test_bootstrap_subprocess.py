"""Phase 1 Step 18: `python -m atp_api.bootstrap` invoked as a real
operating-system subprocess against a real, migrated database - a
verification-only milestone.

`tests/unit/api/test_bootstrap.py` exercises `bootstrap_admin()` directly
against a `FakeUnitOfWork`; `tests/integration/db/test_auth_flows.py::
test_bootstrap_admin_creates_the_first_administrator` calls `bootstrap_admin()`
in-process against real PostgreSQL. Neither spawns the CLI itself - `main()`'s
own env-var reading, `SystemExit` on missing configuration, `load_settings()`,
and real engine construction have never executed as a process the way an
operator actually runs this script. This file's job is narrow: prove that
process, as shipped, actually works - not to change or harden it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

from atp_api.security.rbac import ROLE_ADMINISTRATOR
from tests.integration.db.conftest import delete_user_cascade

_REPO_ROOT = Path(__file__).resolve().parents[3]

_SUBPROCESS_TIMEOUT_SECONDS = 60


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _as_async_psycopg_url(dsn: str) -> str:
    """Mirrors every other integration test file's own copy of this
    helper - no shared module exists for it. `main()` passes `DATABASE_URL`
    straight to `atp_persistence.db.create_engine`, which requires the
    `postgresql+psycopg://` driver scheme, not the plain `postgresql://`
    `TEST_DATABASE_URL` is expressed in."""
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    return dsn


def _bootstrap_env(
    *, owner_dsn: str, redis_url: str, token: str, username: str, password: str
) -> dict[str, str]:
    """`main()` reads `DATABASE_URL` (not a per-service DSN) - the same
    "migration/owner DSN... application services never use the owner
    role" convention `.env.example` documents for this one-shot admin
    script, mirrored here with `owner_dsn`."""
    env = os.environ.copy()
    env.update(
        {
            "SESSION_SECRET_KEY": "a" * 40,
            "DATABASE_URL": _as_async_psycopg_url(owner_dsn),
            "REDIS_URL": redis_url,
            "BOOTSTRAP_ADMIN_TOKEN": token,
            "BOOTSTRAP_ADMIN_USERNAME": username,
            "BOOTSTRAP_ADMIN_PASSWORD": password,
        }
    )
    return env


def _run_bootstrap_cli(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "atp_api.bootstrap"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )


def _require_empty_users_table(owner_connection: psycopg.Connection) -> None:
    """Mirrors `test_auth_flows.py::test_bootstrap_admin_creates_the_first_administrator`'s
    own precondition check: `bootstrap_admin`'s one-time-use invariant is
    "refuse whenever `core.users` has any row," so a leftover row from an
    earlier, improperly-cleaned-up test would make this test's *first*
    invocation fail the way only its *second* invocation should - a
    `pytest.fail`, not a skip, per Step 11's fail-closed gate philosophy
    (a silently skipped assumption is exactly what that gate exists to
    prevent)."""
    with owner_connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM core.users")
        (existing,) = cur.fetchone()  # type: ignore[misc]
    if existing:
        with owner_connection.cursor() as cur:
            cur.execute("SELECT username FROM core.users ORDER BY created_at LIMIT 10")
            leftovers = [row[0] for row in cur.fetchall()]
        owner_connection.rollback()
        pytest.fail(
            f"core.users must be empty for this test but holds {existing} row(s) - "
            f"an earlier test leaked them: {leftovers}"
        )
    owner_connection.rollback()


def test_first_invocation_creates_the_administrator_via_a_real_subprocess(
    migrated_database: str,
    owner_dsn: str,
    redis_url: str,
    owner_connection: psycopg.Connection,
) -> None:
    _require_empty_users_table(owner_connection)
    username = f"itest-bootstrap-cli-{_new_uuid()}"
    env = _bootstrap_env(
        owner_dsn=owner_dsn,
        redis_url=redis_url,
        token="itest-bootstrap-token",
        username=username,
        password="a genuinely strong bootstrap password",
    )

    result = _run_bootstrap_cli(env)

    try:
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "Bootstrap administrator created: user_id=" in result.stdout

        with owner_connection.cursor() as cur:
            cur.execute(
                "SELECT role, must_change_password FROM core.users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
        owner_connection.rollback()
        assert row is not None
        role, must_change_password = row
        assert role == ROLE_ADMINISTRATOR
        assert must_change_password is True
    finally:
        with owner_connection.cursor() as cur:
            cur.execute("SELECT user_id FROM core.users WHERE username = %s", (username,))
            row = cur.fetchone()
        owner_connection.rollback()
        if row is not None:
            delete_user_cascade(owner_connection, str(row[0]))


def test_second_invocation_after_a_successful_bootstrap_fails_with_no_duplicate(
    migrated_database: str,
    owner_dsn: str,
    redis_url: str,
    owner_connection: psycopg.Connection,
) -> None:
    """The one-time-use invariant, proven against the real CLI process, not
    just `bootstrap_admin()` in-process: a second `python -m atp_api.bootstrap`
    invocation, after a genuinely successful first one, must not create a
    second administrator."""
    _require_empty_users_table(owner_connection)
    username = f"itest-bootstrap-cli-2nd-{_new_uuid()}"
    env = _bootstrap_env(
        owner_dsn=owner_dsn,
        redis_url=redis_url,
        token="itest-bootstrap-token-2nd",
        username=username,
        password="a genuinely strong bootstrap password",
    )

    try:
        first = _run_bootstrap_cli(env)
        assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

        second = _run_bootstrap_cli(env)
        assert second.returncode != 0
        # `main()` now catches `BootstrapError` and re-raises it as a
        # `SystemExit` with the exception's own human-readable message -
        # the same clean-exit shape the missing-env-var case below already
        # used, closing the gap the previous milestone deliberately left
        # open (a raw unhandled traceback for this exact case).
        assert "already run" in second.stderr
        assert "Traceback" not in second.stderr

        with owner_connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM core.users WHERE username LIKE %s", (f"{username}%",))
            (count,) = cur.fetchone()  # type: ignore[misc]
        owner_connection.rollback()
        assert count == 1
    finally:
        with owner_connection.cursor() as cur:
            cur.execute("SELECT user_id FROM core.users WHERE username = %s", (username,))
            row = cur.fetchone()
        owner_connection.rollback()
        if row is not None:
            delete_user_cascade(owner_connection, str(row[0]))


def test_missing_bootstrap_env_vars_exits_nonzero_without_a_traceback() -> None:
    """No Docker/database needed - `main()` checks
    `BOOTSTRAP_ADMIN_TOKEN`/`_USERNAME`/`_PASSWORD` and raises a deliberate
    `SystemExit` with a fixed message *before* ever constructing an engine
    or attempting a connection, so `DATABASE_URL`/`REDIS_URL` below only
    need to be syntactically valid for `Settings` (validated first,
    per `load_settings()`), never reachable."""
    env = os.environ.copy()
    env.update(
        {
            "SESSION_SECRET_KEY": "a" * 40,
            "DATABASE_URL": "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere",
            "REDIS_URL": "redis://:nothing@127.0.0.1:1/0",
        }
    )
    for var in ("BOOTSTRAP_ADMIN_TOKEN", "BOOTSTRAP_ADMIN_USERNAME", "BOOTSTRAP_ADMIN_PASSWORD"):
        env.pop(var, None)

    result = _run_bootstrap_cli(env)

    assert result.returncode != 0
    assert (
        "BOOTSTRAP_ADMIN_TOKEN, BOOTSTRAP_ADMIN_USERNAME, and BOOTSTRAP_ADMIN_PASSWORD "
        "must all be set in the environment." in result.stderr
    )
    # Distinguishes this deliberate, documented `SystemExit` from an
    # accidental crash that also happens to exit non-zero - a bare
    # `SystemExit("...")` reaching the interpreter prints only its message,
    # never a traceback, so this line is the difference between "the
    # documented failure path fired" and "something else broke first."
    assert "Traceback" not in result.stderr
