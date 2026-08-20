"""Process entrypoint: `python -m atp_strategy` (Milestone 2C poll loop).

Reads `DATABASE_URL` (via `atp_platform.config.load_settings`) exactly like
every other Phase 1 service - this process is expected to be given the
`atp_strategy` role's DSN in its own environment, never `atp_owner`'s or
any other service's (ops/sql/roles_and_schemas.sql.tmpl).

Runs entirely independently of `atp_worker`: its own poll loop, its own
process, no `core.job_queue` involvement of any kind (ADR-013's boundary
is unaffected by this service's existence - see Milestone 2's own
architecture decision for why job-queue scheduling was rejected).
"""

from __future__ import annotations

import argparse
import asyncio
import os

from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_persistence.db import create_engine, make_session_factory
from atp_platform.config import load_settings
from atp_platform.logging import configure_logging, get_logger
from atp_strategy.registry import DEFAULT_STRATEGY_REGISTRY
from atp_strategy.runner import DEFAULT_EVALUATION_INTERVAL_SECONDS, run_poll_loop
from atp_strategy.uow import strategy_unit_of_work_factory

_logger = get_logger("atp_strategy.main")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="atp_strategy",
        description="Strategy evaluation poll loop (Milestone 2C, ADR-014/ADR-015).",
    )
    parser.add_argument(
        "--evaluation-interval-seconds",
        type=float,
        default=float(
            os.environ.get(
                "ATP_STRATEGY_EVALUATION_INTERVAL_SECONDS", DEFAULT_EVALUATION_INTERVAL_SECONDS
            )
        ),
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> None:
    settings = load_settings()
    configure_logging(service="atp-strategy", level=settings.log_level)
    engine = create_engine(settings.database_url.get_secret_value())
    session_factory = make_session_factory(engine)
    uow_factory = strategy_unit_of_work_factory(session_factory)
    id_generator = UUIDv7Generator()
    clock = UTCClock()
    _logger.info(
        "atp_strategy_starting",
        evaluation_interval_seconds=args.evaluation_interval_seconds,
        registered_strategy_keys=sorted(
            strategy.strategy_key for strategy in DEFAULT_STRATEGY_REGISTRY.all()
        ),
    )
    try:
        await run_poll_loop(
            uow_factory,
            registry=DEFAULT_STRATEGY_REGISTRY,
            id_generator=id_generator,
            clock=clock,
            evaluation_interval_seconds=args.evaluation_interval_seconds,
        )
    finally:
        await engine.dispose()


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
