"""Unit tests for `atp_worker.__main__` (Phase 1 Step 12 Phase B).

`_run()` wires two genuinely infinite coroutines (`run_poll_loop` /
`run_scheduler_loop`, both `max_iterations=None` in production) - every
wiring test here monkeypatches both with an immediately-returning stub
before calling `_run()`, so no test ever actually enters either loop.
Mirrors the `monkeypatch`-per-test style already used in
`tests/unit/test_config.py`, since `atp_worker.__main__` reads
`DATABASE_URL`/`REDIS_URL`/`SESSION_SECRET_KEY` through the same
`load_settings()` every other Phase 1 service does.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
from typing import Any

import pytest

from atp_worker import __main__ as worker_main

_VALID_DATABASE_URL = "postgresql+psycopg://atp_worker:fixture-only@localhost:5432/atp"
_VALID_REDIS_URL = "redis://:fixture-only@localhost:6379/0"
_VALID_SECRET = "a" * 40


@pytest.fixture(autouse=True)
def _valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _VALID_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", _VALID_REDIS_URL)
    monkeypatch.setenv("SESSION_SECRET_KEY", _VALID_SECRET)
    monkeypatch.delenv("ATP_WORKER_POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("ATP_WORKER_INSTANCE_ID", raising=False)


# --- argument parsing -----------------------------------------------------


def test_parse_args_defaults_match_the_runner_and_hostname_pid() -> None:
    args = worker_main._parse_args([])

    assert args.poll_interval_seconds == worker_main.DEFAULT_POLL_INTERVAL_SECONDS
    assert ":" in args.instance_id  # hostname:pid shape


def test_parse_args_honours_env_override_for_poll_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATP_WORKER_POLL_INTERVAL_SECONDS", "9.5")

    args = worker_main._parse_args([])

    assert args.poll_interval_seconds == 9.5


def test_parse_args_honours_env_override_for_instance_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATP_WORKER_INSTANCE_ID", "fixed-instance-1")

    args = worker_main._parse_args([])

    assert args.instance_id == "fixed-instance-1"


def test_parse_args_cli_flags_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATP_WORKER_POLL_INTERVAL_SECONDS", "9.5")
    monkeypatch.setenv("ATP_WORKER_INSTANCE_ID", "env-instance")

    args = worker_main._parse_args(
        ["--poll-interval-seconds", "1.0", "--instance-id", "cli-instance"]
    )

    assert args.poll_interval_seconds == 1.0
    assert args.instance_id == "cli-instance"


def test_default_instance_id_is_hostname_colon_pid() -> None:
    instance_id = worker_main._default_instance_id()

    assert instance_id == f"{socket.gethostname()}:{os.getpid()}"


# --- _run() wiring ----------------------------------------------------


class _FakeEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


def _patch_wiring(
    monkeypatch: pytest.MonkeyPatch,
    *,
    poll_loop_raises: bool = False,
) -> dict[str, Any]:
    calls: dict[str, Any] = {}
    fake_engine = _FakeEngine()

    def fake_create_engine(dsn: str, **_kwargs: Any) -> _FakeEngine:
        calls["database_url"] = dsn
        return fake_engine

    def fake_make_session_factory(engine: object) -> str:
        calls["session_factory_engine"] = engine
        return "session-factory"

    def fake_worker_unit_of_work_factory(session_factory: object) -> str:
        calls["uow_factory_session_factory"] = session_factory
        return "uow-factory"

    async def fake_run_poll_loop(uow_factory: object, **kwargs: Any) -> None:
        calls["run_poll_loop_uow_factory"] = uow_factory
        calls["run_poll_loop_kwargs"] = kwargs
        if poll_loop_raises:
            raise RuntimeError("boom")

    async def fake_run_scheduler_loop(uow_factory: object, **kwargs: Any) -> None:
        calls["run_scheduler_loop_uow_factory"] = uow_factory
        calls["run_scheduler_loop_kwargs"] = kwargs

    monkeypatch.setattr(worker_main, "create_engine", fake_create_engine)
    monkeypatch.setattr(worker_main, "make_session_factory", fake_make_session_factory)
    monkeypatch.setattr(
        worker_main, "worker_unit_of_work_factory", fake_worker_unit_of_work_factory
    )
    monkeypatch.setattr(worker_main, "run_poll_loop", fake_run_poll_loop)
    monkeypatch.setattr(worker_main, "run_scheduler_loop", fake_run_scheduler_loop)
    calls["engine"] = fake_engine
    return calls


def _args(*, poll_interval_seconds: float = 5.0, instance_id: str = "test-instance") -> Any:
    return argparse.Namespace(poll_interval_seconds=poll_interval_seconds, instance_id=instance_id)


def test_run_wires_the_database_url_into_create_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_wiring(monkeypatch)

    asyncio.run(worker_main._run(_args()))

    assert calls["database_url"] == _VALID_DATABASE_URL


def test_run_chains_engine_session_factory_and_uow_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_wiring(monkeypatch)

    asyncio.run(worker_main._run(_args()))

    assert calls["session_factory_engine"] is calls["engine"]
    assert calls["uow_factory_session_factory"] == "session-factory"
    assert calls["run_poll_loop_uow_factory"] == "uow-factory"
    assert calls["run_scheduler_loop_uow_factory"] == "uow-factory"


def test_run_passes_instance_id_and_poll_interval_to_the_runner_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_wiring(monkeypatch)

    asyncio.run(worker_main._run(_args(poll_interval_seconds=1.5, instance_id="worker-7")))

    runner_kwargs = calls["run_poll_loop_kwargs"]
    assert runner_kwargs["instance_id"] == "worker-7"
    assert runner_kwargs["poll_interval_seconds"] == 1.5
    # The scheduler has no poll-interval or instance-id concept of its own
    # (ADR-013 Section 6a: its tick cadence is a fixed constant, not a
    # runtime-configurable value).
    scheduler_kwargs = calls["run_scheduler_loop_kwargs"]
    assert "instance_id" not in scheduler_kwargs
    assert "poll_interval_seconds" not in scheduler_kwargs


def test_run_disposes_the_engine_even_when_a_loop_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_wiring(monkeypatch, poll_loop_raises=True)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(worker_main._run(_args()))

    assert calls["engine"].disposed is True


def test_run_disposes_the_engine_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_wiring(monkeypatch)

    asyncio.run(worker_main._run(_args()))

    assert calls["engine"].disposed is True


def test_run_both_loops_run_concurrently_not_sequentially(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`asyncio.gather`, not two awaited-in-sequence calls - if `_run`
    regressed to sequential awaits, an infinite `run_poll_loop` (the real
    production behaviour, not this test's stub) would starve the
    scheduler forever. This test can't observe blocking behavior directly
    against stubs that both return immediately, so it instead asserts
    both were in fact invoked once each within the same `_run` call -
    the wiring precondition for `gather` to matter at all."""
    order: list[str] = []

    async def fake_run_poll_loop(uow_factory: object, **kwargs: Any) -> None:
        order.append("poll_loop_called")

    async def fake_run_scheduler_loop(uow_factory: object, **kwargs: Any) -> None:
        order.append("scheduler_loop_called")

    monkeypatch.setattr(worker_main, "create_engine", lambda dsn, **_kw: _FakeEngine())
    monkeypatch.setattr(worker_main, "make_session_factory", lambda engine: "session-factory")
    monkeypatch.setattr(
        worker_main, "worker_unit_of_work_factory", lambda session_factory: "uow-factory"
    )
    monkeypatch.setattr(worker_main, "run_poll_loop", fake_run_poll_loop)
    monkeypatch.setattr(worker_main, "run_scheduler_loop", fake_run_scheduler_loop)

    asyncio.run(worker_main._run(_args()))

    assert sorted(order) == ["poll_loop_called", "scheduler_loop_called"]
