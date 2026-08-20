"""Process entrypoint scaffold: `python -m atp_strategy` (Milestone 2B).

Reads `DATABASE_URL` (via `atp_platform.config.load_settings`) exactly like
every other Phase 1 service - this process is expected to be given the
`atp_strategy` role's DSN in its own environment, never `atp_owner`'s or
any other service's (ops/sql/roles_and_schemas.sql.tmpl).

Deliberately does nothing beyond proving the service boundary is wired:
constructs the engine, session factory, and `StrategyUnitOfWork` plumbing,
then disposes and exits. No strategy is evaluated, no proposal is written,
no kill switch is consulted, and no poll loop runs - `atp_strategy.runner`
does not exist yet (Milestone 2C).
"""

from __future__ import annotations

import argparse
import asyncio

from atp_domain.clock import UTCClock
from atp_domain.ids import UUIDv7Generator
from atp_persistence.db import create_engine, make_session_factory
from atp_platform.config import load_settings
from atp_platform.logging import configure_logging, get_logger
from atp_strategy.uow import strategy_unit_of_work

_logger = get_logger("atp_strategy.main")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="atp_strategy",
        description=(
            "Strategy execution boundary service scaffold (Milestone 2B). "
            "Verifies its database wiring and exits - evaluates no "
            "strategy and writes no proposal."
        ),
    )
    return parser.parse_args(argv)


async def _run(_args: argparse.Namespace) -> None:
    settings = load_settings()
    configure_logging(service="atp-strategy", level=settings.log_level)
    engine = create_engine(settings.database_url.get_secret_value())
    session_factory = make_session_factory(engine)
    # Constructed to prove the dependency-injection shape a future runner
    # will use (ADR-013's precedent: Clock/IdGenerator are always injected,
    # never read ambiently) - unused until Milestone 2C.
    _clock = UTCClock()
    _id_generator = UUIDv7Generator()
    try:
        async with strategy_unit_of_work(session_factory):
            pass  # proves the wiring only - nothing is read or written
        _logger.info("atp_strategy_boundary_verified")
    finally:
        await engine.dispose()


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
