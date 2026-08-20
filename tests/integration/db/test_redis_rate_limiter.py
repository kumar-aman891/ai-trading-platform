"""Phase 1 Step 15: `RedisRateLimiter` against a real Redis service.

`tests/unit/api/test_rate_limiter.py` already exercises
`RedisRateLimiter`'s fixed-window/isolation/fail-open logic in full
against an in-memory fake; that fake cannot prove the one thing that
matters most for a *distributed* limiter - that the Lua script genuinely
serializes concurrent increments inside a real Redis server, rather than
merely looking atomic in single-threaded Python. This file's job is
narrow: prove atomicity under real concurrency, prove window rollover
against Redis's own key expiry (not a fake clock), and prove key-prefix
isolation against a real shared connection. The fail-open behavior
against a genuinely unreachable real `redis-py` client needs no Docker at
all, so it lives in `tests/unit/api/test_rate_limiter.py` instead.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import redis as redis_lib

from atp_api.middleware.rate_limit import RedisRateLimiter


def _unique_prefix() -> str:
    return f"it-ratelimit-{uuid.uuid4()}"


def test_concurrent_requests_never_admit_more_than_the_limit(redis_url: str) -> None:
    """The atomicity proof: `limit` admissions and no more, even when many
    requests for the same key race each other through real Redis at once -
    what a non-atomic GET/INCR/EXPIRE sequence could not guarantee."""
    client = redis_lib.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    limiter = RedisRateLimiter(
        client=client, limit=10, window_seconds=30, key_prefix=_unique_prefix()
    )

    async def run() -> list[bool]:
        return await asyncio.gather(
            *(asyncio.to_thread(limiter.allow, "contended-key") for _ in range(40))
        )

    try:
        results = asyncio.run(run())
        assert sum(1 for allowed in results if allowed) == 10
        assert sum(1 for allowed in results if not allowed) == 30
    finally:
        limiter.close()


def test_different_keys_have_independent_budgets_against_real_redis(redis_url: str) -> None:
    client = redis_lib.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    limiter = RedisRateLimiter(
        client=client, limit=1, window_seconds=30, key_prefix=_unique_prefix()
    )

    try:
        assert limiter.allow("a") is True
        assert limiter.allow("b") is True
        assert limiter.allow("a") is False
    finally:
        limiter.close()


def test_window_rolls_over_via_real_redis_key_expiry(redis_url: str) -> None:
    """No fake clock here - the window boundary is enforced by Redis's own
    `PEXPIRE`, proving `_REDIS_INCR_WITH_WINDOW_EXPIRY` sets it correctly
    at window start rather than merely looking correct against a fake."""
    client = redis_lib.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    limiter = RedisRateLimiter(
        client=client, limit=1, window_seconds=1, key_prefix=_unique_prefix()
    )

    try:
        assert limiter.allow("k") is True
        assert limiter.allow("k") is False

        time.sleep(1.2)

        assert limiter.allow("k") is True
    finally:
        limiter.close()


def test_two_limiter_instances_sharing_a_client_do_not_collide_on_key_prefix(
    redis_url: str,
) -> None:
    client = redis_lib.Redis.from_url(redis_url, socket_connect_timeout=3, socket_timeout=3)
    general = RedisRateLimiter(
        client=client, limit=1, window_seconds=30, key_prefix="ratelimit:general"
    )
    login = RedisRateLimiter(
        client=client, limit=1, window_seconds=30, key_prefix="ratelimit:login"
    )
    shared_key = f"shared-{uuid.uuid4()}"

    try:
        assert general.allow(shared_key) is True
        assert login.allow(shared_key) is True
    finally:
        general.close()
