"""Alembic environment.

Reads the migration DSN from the process environment only -
`ATP_MIGRATION_DATABASE_URL`, falling back to `DATABASE_URL` - never from a
literal in `alembic.ini` (security/SECRET_HANDLING.md rule 1: no secret in
a tracked file). Migrations run as the `atp_owner` role
(ops/sql/roles_and_schemas.sql.tmpl's "owner role... used only by the
migration step") - never as `atp_api`/`atp_paper_exec`/`atp_worker`, which
lack the DDL privileges a migration needs.

Uses a plain synchronous engine (`postgresql+psycopg://`, psycopg3's sync
mode) for the migration run itself. The application's runtime engine
(`atp_persistence.db.create_engine`) is async and entirely independent of
this module - migrations and application traffic never share a connection.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from atp_persistence.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _migration_url() -> str:
    url = os.environ.get("ATP_MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "ATP_MIGRATION_DATABASE_URL (or DATABASE_URL) must be set in the "
            "environment - alembic.ini deliberately carries no DSN "
            "(security/SECRET_HANDLING.md)."
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database connection (`alembic
    upgrade head --sql`)."""
    context.configure(
        url=_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema="core",
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _migration_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="core",
            include_schemas=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
