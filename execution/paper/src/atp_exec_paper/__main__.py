"""Process entrypoint: `python -m atp_exec_paper` (poll loop - the
production mode, ADR-011) or `python -m atp_exec_paper --proposal-id <id>`
(one-shot; mirrors `python -m atp_api.bootstrap`'s CLI precedent).

Reads `DATABASE_URL` (via `atp_platform.config.load_settings`) exactly like
every other Phase 1 service - this process is expected to be given the
`atp_paper_exec` role's DSN in its own environment, never `atp_owner`'s or
`atp_api`'s (ops/sql/roles_and_schemas.sql.tmpl).
"""

from __future__ import annotations

import argparse
import asyncio
import os

from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_exec_paper.gateway import (
    DEFAULT_CLAIM_BATCH_SIZE,
    DEFAULT_POLL_INTERVAL_SECONDS,
    run_once,
    run_poll_loop,
)
from atp_persistence.db import create_engine, make_session_factory
from atp_platform.config import load_settings
from atp_platform.logging import configure_logging, get_logger

_logger = get_logger("atp_exec_paper.main")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="atp_exec_paper")
    parser.add_argument(
        "--proposal-id",
        default=None,
        help="Run once for a single proposal_id, then exit. Omit to run the poll loop.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=float(
            os.environ.get("ATP_PAPER_EXEC_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS)
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("ATP_PAPER_EXEC_CLAIM_BATCH_SIZE", DEFAULT_CLAIM_BATCH_SIZE)),
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> None:
    settings = load_settings()
    configure_logging(service="atp-exec-paper", level=settings.log_level)
    engine = create_engine(settings.database_url.get_secret_value())
    session_factory = make_session_factory(engine)
    id_generator = UUIDv7Generator()
    clock = UTCClock()
    try:
        if args.proposal_id:
            outcome = await run_once(
                session_factory, args.proposal_id, id_generator=id_generator, clock=clock
            )
            _logger.info(
                "proposal_executed",
                proposal_id=outcome.proposal_id,
                outcome=str(outcome.decision_outcome),
                order_id=outcome.order_id,
                already_claimed=outcome.already_claimed,
            )
        else:
            await run_poll_loop(
                session_factory,
                id_generator=id_generator,
                clock=clock,
                poll_interval_seconds=args.poll_interval_seconds,
                batch_size=args.batch_size,
            )
    finally:
        await engine.dispose()


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
