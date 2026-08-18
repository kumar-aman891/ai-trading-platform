"""`GET /api/v1/system/status` application logic.

`mode` is read from `Settings.trading_mode` only - `Settings` itself
refuses to construct with anything but `TRADING_MODE=PAPER`
(`atp_platform.config.Settings._validate_trading_mode`), so there is no
value this function could report other than `"PAPER"`, and nothing here
accepts a caller-supplied override.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atp_api.services.dependencies import database_check
from atp_persistence.db import read_only_session
from atp_platform.config import Settings


@dataclass(frozen=True, slots=True)
class DependencyStatusView:
    name: str
    ok: bool


@dataclass(frozen=True, slots=True)
class SystemStatusView:
    mode: str
    version: str
    environment: str
    migration_version: str | None
    degraded: bool
    dependencies: tuple[DependencyStatusView, ...]


async def _read_migration_version(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> str | None:
    if session_factory is None:
        return None
    try:
        async with read_only_session(session_factory) as session:
            result = await session.execute(text("SELECT version_num FROM core.alembic_version"))
            row = result.first()
            return str(row[0]) if row is not None else None
    except SQLAlchemyError:
        return None


async def build_system_status(
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] | None,
    app_version: str,
) -> SystemStatusView:
    db_result = await database_check(session_factory)
    migration_version = await _read_migration_version(session_factory) if db_result.ok else None
    return SystemStatusView(
        mode=settings.trading_mode,
        version=app_version,
        environment=settings.environment,
        migration_version=migration_version,
        degraded=not db_result.ok,
        dependencies=(DependencyStatusView(name=db_result.name, ok=db_result.ok),),
    )
