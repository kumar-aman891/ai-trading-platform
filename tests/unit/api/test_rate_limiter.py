"""RATE LIMITER: the `RateLimiter` abstraction, the deterministic
in-memory implementation, the Redis-backed distributed implementation
(Phase 1 Step 15), and its wiring as ASGI middleware.

`RedisRateLimiter`'s own tests below use `_FakeRedisClient` - an
in-memory stand-in that implements exactly the two `redis.Redis` methods
`RedisRateLimiter` calls (`register_script`, `close`) and reproduces the
Lua script's INCR-with-conditional-expiry semantics in pure Python against
an injectable clock - rather than the real `redis-py` client, so these
tests need no Docker and stay deterministic under a controlled clock, the
same reason `InMemoryRateLimiter`'s own tests above inject a `FrozenClock`
instead of using wall-clock time. `RedisRateLimiter` genuinely exercised
against a real Redis service lives in
`tests/integration/db/test_redis_rate_limiter.py` (Docker-gated)."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import redis as redis_lib
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from atp_api.app import create_app
from atp_api.middleware import rate_limit as rate_limit_module
from atp_api.middleware.rate_limit import InMemoryRateLimiter, RateLimiter, RedisRateLimiter
from atp_domain.clock import FrozenClock
from atp_platform.config import Settings


class _FakeClock:
    """A plain mutable time source (seconds since epoch) - deliberately
    not `atp_domain.clock.Clock`, since `RedisRateLimiter` (like the real
    `redis.Redis` client it stands in for) has no `Clock` port; Redis
    itself owns key expiry."""

    def __init__(self, *, seconds: float = 1_700_000_000.0) -> None:
        self.seconds = seconds

    def advance(self, seconds: float) -> None:
        self.seconds += seconds


class _FakeRedisScript:
    """Reproduces `_REDIS_INCR_WITH_WINDOW_EXPIRY`'s semantics - INCR the
    counter for `keys[0]`, and if this call started a fresh window (the
    prior entry, if any, has already expired), set its expiry once - in
    pure Python against `_FakeClock`, standing in for what the real Lua
    script does atomically inside Redis."""

    def __init__(self, store: dict[str, tuple[int, float]], clock: _FakeClock) -> None:
        self._store = store
        self._clock = clock

    def __call__(self, *, keys: list[str], args: list[int]) -> int:
        key = keys[0]
        window_ms = int(args[0])
        entry = self._store.get(key)
        if entry is not None and entry[1] <= self._clock.seconds:
            entry = None
        if entry is None:
            current = 1
            self._store[key] = (current, self._clock.seconds + window_ms / 1000)
        else:
            current = entry[0] + 1
            self._store[key] = (current, entry[1])
        return current


class _FakeRedisClient:
    """The subset of `redis.Redis`'s interface `RedisRateLimiter` uses."""

    def __init__(self, *, clock: _FakeClock | None = None) -> None:
        self.store: dict[str, tuple[int, float]] = {}
        self.clock = clock or _FakeClock()
        self.closed = False

    def register_script(self, _script: str) -> Callable[..., int]:
        return _FakeRedisScript(self.store, self.clock)

    def close(self) -> None:
        self.closed = True


class _AlwaysFailingRedisClient:
    """Simulates an unreachable Redis - every script invocation raises a
    `RedisError` subclass, the exact condition `RedisRateLimiter.allow`
    must fail open on."""

    def __init__(self) -> None:
        self.closed = False

    def register_script(self, _script: str) -> Callable[..., int]:
        def _raise(**_kwargs: Any) -> int:
            raise RedisConnectionError("simulated Redis outage")

        return _raise

    def close(self) -> None:
        self.closed = True


def test_redis_rate_limiter_satisfies_the_protocol() -> None:
    limiter: RateLimiter = RedisRateLimiter(
        client=_FakeRedisClient(), limit=1, window_seconds=60, key_prefix="test"
    )
    assert isinstance(limiter, RateLimiter)


def test_redis_limiter_allows_requests_up_to_the_limit_then_rejects() -> None:
    limiter = RedisRateLimiter(
        client=_FakeRedisClient(), limit=3, window_seconds=60, key_prefix="test"
    )

    results = [limiter.allow("k") for _ in range(4)]

    assert results == [True, True, True, False]


def test_redis_limiter_different_keys_have_independent_budgets() -> None:
    limiter = RedisRateLimiter(
        client=_FakeRedisClient(), limit=1, window_seconds=60, key_prefix="test"
    )

    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False


def test_redis_limiter_two_instances_sharing_a_client_do_not_collide_on_key_prefix() -> None:
    """The thing `key_prefix` exists for: two `RedisRateLimiter`s (e.g.
    `atp_api.app.create_app`'s general and login limiters) pointed at the
    same Redis must not share a counter for the same literal `key`."""
    client = _FakeRedisClient()
    general = RedisRateLimiter(
        client=client, limit=1, window_seconds=60, key_prefix="ratelimit:general"
    )
    login = RedisRateLimiter(
        client=client, limit=1, window_seconds=60, key_prefix="ratelimit:login"
    )

    assert general.allow("127.0.0.1") is True
    assert login.allow("127.0.0.1") is True  # would be False if the namespaces collided


def test_redis_limiter_budget_resets_after_the_window_elapses() -> None:
    clock = _FakeClock()
    limiter = RedisRateLimiter(
        client=_FakeRedisClient(clock=clock), limit=1, window_seconds=60, key_prefix="test"
    )

    assert limiter.allow("k") is True
    assert limiter.allow("k") is False

    clock.advance(61)
    assert limiter.allow("k") is True


def test_redis_limiter_repeated_calls_within_a_window_keep_being_rejected() -> None:
    limiter = RedisRateLimiter(
        client=_FakeRedisClient(), limit=1, window_seconds=60, key_prefix="test"
    )

    assert limiter.allow("k") is True
    results = [limiter.allow("k") for _ in range(5)]

    assert results == [False, False, False, False, False]


def test_redis_limiter_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="limit"):
        RedisRateLimiter(client=_FakeRedisClient(), limit=0, window_seconds=60, key_prefix="test")


def test_redis_limiter_window_seconds_must_be_positive() -> None:
    with pytest.raises(ValueError, match="window_seconds"):
        RedisRateLimiter(client=_FakeRedisClient(), limit=1, window_seconds=0, key_prefix="test")


def test_redis_limiter_fails_open_when_redis_is_unavailable() -> None:
    """The documented, deliberate failure semantics (`RedisRateLimiter`'s
    own docstring): a request proceeds rather than being blocked by an
    unrelated infrastructure outage."""
    limiter = RedisRateLimiter(
        client=_AlwaysFailingRedisClient(), limit=1, window_seconds=60, key_prefix="test"
    )

    assert limiter.allow("k") is True
    assert limiter.allow("k") is True  # still open on every subsequent call, not just the first


def test_redis_limiter_close_closes_the_underlying_client() -> None:
    client = _FakeRedisClient()
    limiter = RedisRateLimiter(client=client, limit=1, window_seconds=60, key_prefix="test")

    limiter.close()

    assert client.closed is True


def test_redis_limiter_fails_open_against_a_genuinely_unreachable_real_client() -> None:
    """No Docker/fixture needed - a real `redis.Redis` pointed at a port
    nothing listens on, proving the fail-open path against the real
    `redis-py` client type and its real `RedisError` hierarchy, not only
    against `_AlwaysFailingRedisClient`'s simulated one. The genuinely
    real-Redis-service variants of this proof (window rollover, atomicity
    under concurrency) live in
    `tests/integration/db/test_redis_rate_limiter.py` instead - this one
    specifically needs no reachable Redis at all, so it belongs here."""
    unreachable_client = redis_lib.Redis.from_url(
        "redis://:wrong-password@127.0.0.1:1/0",
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    limiter = RedisRateLimiter(
        client=unreachable_client, limit=1, window_seconds=60, key_prefix="unit"
    )

    assert limiter.allow("k") is True
    assert limiter.allow("k") is True


def test_in_memory_rate_limiter_satisfies_the_protocol() -> None:
    limiter: RateLimiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    assert isinstance(limiter, RateLimiter)


def test_allows_requests_up_to_the_limit_then_rejects() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = InMemoryRateLimiter(limit=3, window_seconds=60, clock=clock)

    results = [limiter.allow("k") for _ in range(4)]

    assert results == [True, True, True, False]


def test_different_keys_have_independent_budgets() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False


def test_budget_resets_after_the_window_elapses() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.allow("k") is True
    assert limiter.allow("k") is False

    clock.advance(timedelta(seconds=61))
    assert limiter.allow("k") is True


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError, match="limit"):
        InMemoryRateLimiter(limit=0, window_seconds=60)


def test_window_seconds_must_be_positive() -> None:
    with pytest.raises(ValueError, match="window_seconds"):
        InMemoryRateLimiter(limit=1, window_seconds=0)


def test_middleware_returns_429_once_the_limit_is_exceeded(settings: Settings) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = InMemoryRateLimiter(limit=2, window_seconds=60, clock=clock)
    client = TestClient(create_app(settings=settings, rate_limiter=limiter))

    statuses = [client.get("/healthz").status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


def test_rate_limited_response_has_the_standard_error_shape(settings: Settings) -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60, clock=clock)
    client = TestClient(create_app(settings=settings, rate_limiter=limiter))

    client.get("/healthz")
    response = client.get("/healthz")

    assert response.status_code == 429
    body = response.json()
    assert body["code"] == "RATE_LIMIT_EXCEEDED"
    assert "correlation_id" in body


def test_rate_limiter_does_not_affect_domain_semantics() -> None:
    """The limiter's `allow()` is a pure yes/no gate with no reference to
    any domain type - importing it must not pull in atp_domain trading
    types beyond the generic Clock port."""
    source = inspect.getsource(rate_limit_module)
    for forbidden in ("TradeProposal", "RiskDecision", "Order(", "ApprovedOrderIntent"):
        assert forbidden not in source
