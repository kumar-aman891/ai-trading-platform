"""Bootstrap-admin process (Phase 1 Step 8).

Deliberately *not* an HTTP route - `docs/schemas/user.md` requires "no
default/seeded user in any migration," and adding a self-registration
endpoint would be a new, permanent, unauthenticated attack surface for
exactly one operation that should only ever happen once. Instead this is a
one-shot script (`python -m atp_api.bootstrap`), gated by a one-time
secret from the environment (`ApiSettings.bootstrap_admin_token`) that the
operator sets only for the duration of that one run.

One-time use is enforced by a single invariant, not by tracking "has this
token been used before": bootstrap refuses whenever `core.users` already
has at least one row (`SqlAlchemyUserRepository.count`). The first
successful bootstrap makes every subsequent invocation - with the same
token or a different one - permanently refuse, which is what "the
temporary bootstrap mechanism must not silently remain permanently
usable" requires. Nothing about the token, the username, or the password
is ever logged (the redaction pipeline would also catch a `token`/
`password` key if one somehow reached a log call, but this module never
attempts to log any of them in the first place).
"""

from __future__ import annotations

import asyncio
import os
import uuid

from atp_api.config import load_api_settings
from atp_api.security.passwords import hash_password
from atp_api.security.rbac import ROLE_ADMINISTRATOR
from atp_api.security.tokens import constant_time_equals
from atp_domain.audit import ACTION_BOOTSTRAP_ADMIN_CREATED, AuditEvent
from atp_domain.clock import Clock, UTCClock
from atp_domain.ids import IdGenerator, UUIDv7Generator
from atp_domain.types import ActorType, EventId
from atp_persistence.db import UnitOfWork, create_engine, make_session_factory, unit_of_work
from atp_platform.config import load_settings


class BootstrapError(RuntimeError):
    """Base class for every bootstrap-admin failure."""


class BootstrapNotConfiguredError(BootstrapError):
    """No `BOOTSTRAP_ADMIN_TOKEN` was configured on `ApiSettings` -
    bootstrap is disabled by default."""


class BootstrapTokenInvalidError(BootstrapError):
    """The provided token did not match the configured one."""


class BootstrapAlreadyCompletedError(BootstrapError):
    """`core.users` already has at least one row - bootstrap is single-use."""


async def bootstrap_admin(
    uow: UnitOfWork,
    *,
    provided_token: str,
    expected_token: str | None,
    username: str,
    password: str,
    clock: Clock,
    id_generator: IdGenerator,
    correlation_id: str,
) -> str:
    """Returns the new administrator's `user_id` on success."""
    if expected_token is None:
        raise BootstrapNotConfiguredError(
            "BOOTSTRAP_ADMIN_TOKEN is not configured; bootstrap is disabled."
        )
    if not constant_time_equals(provided_token, expected_token):
        raise BootstrapTokenInvalidError("The provided bootstrap token is invalid.")

    existing_users = await uow.users.count()
    if existing_users > 0:
        raise BootstrapAlreadyCompletedError(
            "core.users already has at least one row; bootstrap has already run."
        )

    now = clock.now()
    user_id = id_generator.new_id()
    await uow.users.create(
        user_id=user_id,
        username=username,
        password_hash=hash_password(password),
        role=ROLE_ADMINISTRATOR,
        must_change_password=True,
        created_at=now,
        updated_at=now,
    )
    await uow.audit.save(
        AuditEvent(
            event_id=EventId(id_generator.new_id()),
            correlation_id=correlation_id,
            occurred_at=now,
            recorded_at=now,
            actor_type=ActorType.SYSTEM,
            actor_id=user_id,
            action=ACTION_BOOTSTRAP_ADMIN_CREATED,
            mode=None,
            strategy_id=None,
            strategy_version=None,
            instrument_id=None,
            decision="APPROVED",
        )
    )
    return user_id


async def _run(*, database_url: str, token: str, username: str, password: str) -> None:
    api_settings = load_api_settings()
    expected_token = (
        api_settings.bootstrap_admin_token.get_secret_value()
        if api_settings.bootstrap_admin_token is not None
        else None
    )
    engine = create_engine(database_url)
    session_factory = make_session_factory(engine)
    try:
        async with unit_of_work(session_factory) as uow:
            user_id = await bootstrap_admin(
                uow,
                provided_token=token,
                expected_token=expected_token,
                username=username,
                password=password,
                clock=UTCClock(),
                id_generator=UUIDv7Generator(),
                correlation_id=str(uuid.uuid4()),
            )
        print(f"Bootstrap administrator created: user_id={user_id}")
    finally:
        await engine.dispose()


def main() -> None:
    settings = load_settings()
    token = os.environ.get("BOOTSTRAP_ADMIN_TOKEN")
    username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME")
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if not token or not username or not password:
        raise SystemExit(
            "BOOTSTRAP_ADMIN_TOKEN, BOOTSTRAP_ADMIN_USERNAME, and "
            "BOOTSTRAP_ADMIN_PASSWORD must all be set in the environment."
        )
    asyncio.run(
        _run(
            database_url=settings.database_url.get_secret_value(),
            token=token,
            username=username,
            password=password,
        )
    )


if __name__ == "__main__":
    main()
