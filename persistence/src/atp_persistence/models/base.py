"""Declarative base and shared column helpers for every ORM model.

Kept deliberately small: a naming convention (so every constraint Alembic
autogenerate would ever compare has a deterministic name instead of
Postgres's default anonymous one) and a couple of typed column factories for
the two column shapes that repeat on nearly every table in docs/schemas/ -
a UUIDv7 primary key minted application-side, and a timezone-aware UTC
timestamp. Nothing here is domain logic; ORM models built on this base stay
entirely inside atp_persistence (ADR-009, rules/01-architecture.md).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import MetaData
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column

# Deterministic constraint names. Without this, Postgres assigns anonymous
# names to CHECK constraints and Alembic autogenerate produces unstable
# diffs across environments.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Root of the ORM mapping. One `MetaData` shared by every schema
    module (`atp_persistence.models.core/audit/paper` each declare
    `__table_args__ = {"schema": "..."}` on their own classes) so Alembic's
    `target_metadata` sees every table in one place."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk(column_name: str) -> MappedColumn[str]:
    """A UUIDv7 primary key column. `as_uuid=False` so SQLAlchemy hands
    back a plain `str`, matching every domain identifier
    (`NewType("XId", str)` in atp_domain.types) without a conversion step
    at the mapper boundary. Never server-generated - IDs are always minted
    application-side via atp_domain.ids.IdGenerator (docs/schemas/README.md)."""
    return mapped_column(UUID(as_uuid=False), primary_key=True, name=column_name)


def uuid_column(*, nullable: bool = False) -> MappedColumn[str]:
    """A non-primary-key `uuid` column (foreign keys, references)."""
    return mapped_column(UUID(as_uuid=False), nullable=nullable)


def utc_timestamp() -> MappedColumn[datetime]:
    """A required `timestamptz` column. `timezone=True` makes Postgres
    store/return UTC-normalized values; the application is still
    responsible for only ever passing timezone-aware `datetime` objects
    (enforced throughout atp_domain's `__post_init__` methods)."""
    return mapped_column(TIMESTAMP(timezone=True), nullable=False)


def utc_timestamp_nullable() -> MappedColumn[datetime | None]:
    """An optional `timestamptz` column."""
    return mapped_column(TIMESTAMP(timezone=True), nullable=True)
