"""BOOTSTRAP: the one-time bootstrap-admin process
(`atp_api.bootstrap.bootstrap_admin`), exercised directly against a fake
`UnitOfWork` - no Docker, no HTTP route (there isn't one, by design).

Repository protocols declare `async def`, so these tests drive them with
`asyncio.run()` inside ordinary sync test functions, matching
`tests/integration/db/test_repositories.py`'s existing convention (this
repo has no async test-runner plugin installed).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

import atp_api.bootstrap as bootstrap_module
from atp_api.bootstrap import (
    BootstrapAlreadyCompletedError,
    BootstrapNotConfiguredError,
    BootstrapTokenInvalidError,
    bootstrap_admin,
)
from atp_api.security.rbac import ROLE_ADMINISTRATOR
from atp_domain.audit import ACTION_BOOTSTRAP_ADMIN_CREATED
from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from tests.unit.api.fakes import FakeUnitOfWork

_CLOCK = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))


async def _run_bootstrap(uow: FakeUnitOfWork, *, provided_token: str, expected_token: str | None):
    return await bootstrap_admin(
        uow,
        provided_token=provided_token,
        expected_token=expected_token,
        username="root",
        password="a genuinely strong bootstrap password",
        clock=_CLOCK,
        id_generator=SequentialIdGenerator(),
        correlation_id="corr-1",
    )


def test_bootstrap_refuses_when_no_token_is_configured() -> None:
    uow = FakeUnitOfWork()

    async def run() -> None:
        with pytest.raises(BootstrapNotConfiguredError):
            await _run_bootstrap(uow, provided_token="whatever", expected_token=None)
        assert await uow.users.count() == 0

    asyncio.run(run())


def test_bootstrap_refuses_a_wrong_token() -> None:
    uow = FakeUnitOfWork()

    async def run() -> None:
        with pytest.raises(BootstrapTokenInvalidError):
            await _run_bootstrap(uow, provided_token="wrong", expected_token="correct-token")
        assert await uow.users.count() == 0

    asyncio.run(run())


def test_bootstrap_succeeds_with_the_correct_token() -> None:
    uow = FakeUnitOfWork()

    async def run() -> None:
        user_id = await _run_bootstrap(
            uow, provided_token="correct-token", expected_token="correct-token"
        )
        user = await uow.users.get_by_id(user_id)
        assert user is not None
        assert user.role == ROLE_ADMINISTRATOR
        assert user.must_change_password is True
        assert user.password_hash != "a genuinely strong bootstrap password"

    asyncio.run(run())


def test_bootstrap_never_stores_a_plaintext_password() -> None:
    uow = FakeUnitOfWork()

    async def run() -> None:
        user_id = await _run_bootstrap(uow, provided_token="t", expected_token="t")
        user = await uow.users.get_by_id(user_id)
        assert user is not None
        assert "genuinely strong bootstrap password" not in user.password_hash

    asyncio.run(run())


def test_bootstrap_records_an_audit_event() -> None:
    uow = FakeUnitOfWork()

    async def run() -> None:
        user_id = await _run_bootstrap(uow, provided_token="t", expected_token="t")
        events = [e for e in uow.audit.events if e.action == ACTION_BOOTSTRAP_ADMIN_CREATED]
        assert len(events) == 1
        assert events[0].actor_id == user_id

    asyncio.run(run())


def test_bootstrap_refuses_to_run_a_second_time_even_with_the_correct_token() -> None:
    """One-time use is enforced by "core.users already has a row," not by
    tracking token usage - so a second run with the *same still-valid*
    token still refuses."""
    uow = FakeUnitOfWork()

    async def run() -> None:
        await _run_bootstrap(uow, provided_token="t", expected_token="t")
        with pytest.raises(BootstrapAlreadyCompletedError):
            await _run_bootstrap(uow, provided_token="t", expected_token="t")
        assert await uow.users.count() == 1

    asyncio.run(run())


def test_main_translates_a_bootstrap_error_into_a_clean_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`main()`'s `except BootstrapError` -> `SystemExit(str(exc))`
    translation, proven without Docker or a real database. `main()` can
    only reach a real `BootstrapAlreadyCompletedError` by way of an actual
    `core.users` row, which needs Postgres - so this replaces the existing
    module-level `_run` (not a new abstraction) with a stub that raises
    the exact exception a real second invocation would, isolating the
    translation itself from the database round trip
    `tests/integration/db/test_bootstrap_subprocess.py` already covers
    end-to-end."""
    monkeypatch.setenv("SESSION_SECRET_KEY", "a" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:nothing@127.0.0.1:1/nowhere")
    monkeypatch.setenv("REDIS_URL", "redis://:nothing@127.0.0.1:1/0")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_TOKEN", "t")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_USERNAME", "root")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "a genuinely strong bootstrap password")

    async def _fake_run(**_kwargs: object) -> None:
        raise BootstrapAlreadyCompletedError(
            "core.users already has at least one row; bootstrap has already run."
        )

    monkeypatch.setattr(bootstrap_module, "_run", _fake_run)

    with pytest.raises(SystemExit) as exc_info:
        bootstrap_module.main()

    assert "already run" in str(exc_info.value)
