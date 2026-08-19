"""Process entrypoint: `python -m atp_worker` - runs the claim/execute poll
loop (`atp_worker.runner.run_poll_loop`) and the recurring-job scheduler
(`atp_worker.scheduler.run_scheduler_loop`) concurrently, for the process
lifetime, with no HTTP surface of any kind (ADR-013 §1).

Reads `DATABASE_URL` (via `atp_platform.config.load_settings`) exactly
like every other Phase 1 service - this process is expected to be given
the `atp_worker` role's DSN in its own environment, never `atp_owner`'s,
`atp_api`'s, or `atp_paper_exec`'s (`ops/sql/roles_and_schemas.sql.tmpl`),
mirroring `atp_exec_paper.__main__`'s own docstring for that process
exactly (ADR-013 §10).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket

from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_persistence.db import create_engine, make_session_factory
from atp_platform.config import load_settings
from atp_platform.logging import configure_logging, get_logger
from atp_worker.runner import DEFAULT_POLL_INTERVAL_SECONDS, run_poll_loop
from atp_worker.scheduler import SCHEDULE_TICK_SECONDS, run_scheduler_loop
from atp_worker.uow import worker_unit_of_work_factory

_logger = get_logger("atp_worker.main")


def _default_instance_id() -> str:
    """`core.job_queue.locked_by` (`docs/schemas/job_queue.md`) wants "a
    worker instance identifier" and specifies no format - hostname+pid is
    unique per process without adding any new configuration surface, and
    is overridable below for anyone running multiple instances behind a
    fixed identifier scheme."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="atp_worker")
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=float(
            os.environ.get("ATP_WORKER_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
        ),
        help="Claim/execute poll cadence (ADR-013 §6). Does not affect "
        "the scheduler's fixed tick.",
    )
    parser.add_argument(
        "--instance-id",
        default=os.environ.get("ATP_WORKER_INSTANCE_ID", _default_instance_id()),
        help="Identifier stored in core.job_queue.locked_by while this "
        "process holds a claim. Defaults to hostname:pid.",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> None:
    settings = load_settings()
    configure_logging(service="atp-worker", level=settings.log_level)
    engine = create_engine(settings.database_url.get_secret_value())
    session_factory = make_session_factory(engine)
    uow_factory = worker_unit_of_work_factory(session_factory)
    id_generator = UUIDv7Generator()
    clock = UTCClock()

    _logger.info(
        "atp_worker_starting",
        instance_id=args.instance_id,
        poll_interval_seconds=args.poll_interval_seconds,
        scheduler_tick_seconds=SCHEDULE_TICK_SECONDS,
    )
    try:
        await asyncio.gather(
            run_poll_loop(
                uow_factory,
                clock=clock,
                id_generator=id_generator,
                instance_id=args.instance_id,
                poll_interval_seconds=args.poll_interval_seconds,
            ),
            run_scheduler_loop(
                uow_factory,
                clock=clock,
                id_generator=id_generator,
            ),
        )
    finally:
        await engine.dispose()


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_run(args))
    except Exception:
        _logger.exception("atp_worker_startup_failed")
        raise


if __name__ == "__main__":
    main()
