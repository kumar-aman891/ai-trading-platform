"""API-level rate limiting: an abstraction (`RateLimiter`) plus two
implementations - the Step 7 in-process `InMemoryRateLimiter` and the
Phase 1 Step 15 distributed `RedisRateLimiter` - and the ASGI middleware
that applies whichever one is wired in to every request.

Nothing in the middleware or in route/service code depends on either
implementation specifically, only on the `RateLimiter` Protocol, so
swapping the implementation touches only `atp_api.app.create_app`'s (and,
for the real deployed process, `atp_api.main`'s) construction site, not
routes or business logic. No limiter implementation touches domain
semantics (order/proposal/risk state) - it only ever decides whether to
let an HTTP request proceed.

`InMemoryRateLimiter` remains the default `atp_api.app.create_app` builds
when no limiter is explicitly supplied: it is what every Docker-free unit
test in `tests/unit/api/` relies on (directly or via the shared `app`
fixture), and it is still the correct choice for genuinely single-process
local development, where there is no cross-process budget to share.
`RedisRateLimiter` is not the default for that reason - it is explicitly
constructed and injected via `create_app`'s `rate_limiter`/
`login_rate_limiter` parameters, which is what `atp_api.main` (the one
real production entrypoint) does.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Protocol, runtime_checkable

import redis
from redis.exceptions import RedisError

from atp_domain.clock import Clock, UTCClock
from atp_platform.logging import get_logger
from atp_platform.metrics import counter

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

KeyFunc = Callable[[Scope], str]


@runtime_checkable
class RateLimiter(Protocol):
    def allow(self, key: str) -> bool:
        """True if a request keyed by `key` may proceed right now. Purely
        a yes/no decision - no side effect on anything outside the
        limiter's own bookkeeping."""
        ...


class InMemoryRateLimiter:
    """Fixed-window counter, per key, held in process memory. Not shared
    across worker processes - the correct choice for a genuinely
    single-process deployment or a Docker-free test, but not for a
    multi-process one; see `RedisRateLimiter` below and the module
    docstring. Deterministic under test via an injected `Clock`
    (`atp_domain.clock.FrozenClock`), never wall-clock `time.time()`
    directly, per rules/05-testing.md's clock-injection requirement."""

    def __init__(self, *, limit: int, window_seconds: float, clock: Clock | None = None) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive.")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock or UTCClock()
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock.now().timestamp()
        window_start, count = self._windows.get(key, (now, 0))
        if now - window_start >= self._window_seconds:
            window_start, count = now, 0
        if count >= self._limit:
            self._windows[key] = (window_start, count)
            return False
        self._windows[key] = (window_start, count + 1)
        return True


_REDIS_UNAVAILABLE_TOTAL = counter(
    "atp_api_rate_limiter_redis_unavailable_total",
    "Rate-limit checks that failed open because Redis was unreachable, by limiter instance.",
    labelnames=("limiter",),
)

# Same order of magnitude as tests/integration/db/conftest.py's own
# CONNECT_TIMEOUT_SECONDS for Postgres - bounds how long a single request
# can stall behind an unreachable Redis before RedisRateLimiter.allow()
# gives up and fails open, rather than inheriting the OS's much longer
# default TCP connect timeout.
REDIS_SOCKET_TIMEOUT_SECONDS = 3.0

_REDIS_INCR_WITH_WINDOW_EXPIRY = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
    redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
return current
"""


class RedisRateLimiter:
    """Fixed-window counter shared across every `atp_api` process via
    Redis - the distributed counterpart `InMemoryRateLimiter`'s own
    docstring pointed at. Same public contract (`allow(key: str) -> bool`),
    same fixed-window semantics (a key gets `limit` admissions per
    `window_seconds`, then every further call in that window is rejected
    until the window rolls over), same per-key isolation - only the
    storage moves from an in-process `dict` to Redis.

    Atomicity: a separate GET, INCR, and EXPIRE would race under
    concurrent requests for the same key - two requests could both read
    the pre-increment count and both be admitted past `limit`. `INCR` is
    already atomic in Redis; the one thing three separate commands cannot
    give is "set this key's expiry, but only if this call is the one that
    just created it" atomically with the increment. A single Lua script
    (`EVAL`/`EVALSHA`, itself always executed atomically by Redis) does
    both in one round trip: increment, and if the result is `1` - meaning
    this call just started a fresh window, exactly as `InMemoryRateLimiter`
    starts one when `now - window_start >= window_seconds` - set the
    expiry once, at the moment the window starts. A key's TTL is never
    reset on later calls in the same window, matching
    `InMemoryRateLimiter`'s own fixed (not sliding) window.

    Because Redis is one shared keyspace (unlike two independent Python
    `dict`s), two `RedisRateLimiter` instances must not collide on the
    same counter - `key_prefix` gives each instance its own namespace
    (`atp_api.app.create_app` uses `"ratelimit:general"` and
    `"ratelimit:login"`), preserving instance-level isolation that
    `InMemoryRateLimiter` gets for free.

    Client: a synchronous `redis.Redis`, not `redis.asyncio.Redis` -
    `RateLimiter.allow` is a synchronous method (`RateLimitMiddleware` and
    `atp_api.deps.enforce_login_rate_limit` both call it without `await`,
    and `tests/unit/api/test_rate_limiter.py` asserts a plain `bool`
    return), and changing that contract would mean awaiting it through
    the ASGI middleware and every existing caller - a larger redesign than
    this milestone's scope. A round trip to a co-located Redis for one
    `EVALSHA` is sub-millisecond in practice; `REDIS_SOCKET_TIMEOUT_SECONDS`
    bounds the worst case.

    Failure semantics: fails OPEN (returns `True`) on any `RedisError`
    (connection refused, timeout, ...), not closed. This is deliberately
    not the trading risk engine's "reject when indeterminate" rule
    (rules/02-live-trading.md) - that rule exists because an indeterminate
    live-trading check risks capital; rate limiting is a defense-in-depth
    *availability* control layered on top of authentication, RBAC, and
    CSRF, none of which depend on Redis and all of which keep enforcing
    normally during a Redis outage. Failing closed here would turn a
    transient outage of an unpersisted cache (docker-compose.yml: no RDB,
    no AOF - Redis holds nothing durable by design) into a full API outage,
    which is a strictly worse outcome than temporarily admitting every
    request. Every fail-open event is logged at WARNING
    (`rate_limiter_redis_unavailable_fail_open`) and counted
    (`atp_api_rate_limiter_redis_unavailable_total`), so the degradation
    is visible to operators rather than silent.
    """

    def __init__(
        self,
        *,
        client: redis.Redis,
        limit: int,
        window_seconds: float,
        key_prefix: str,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive.")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive.")
        self._client = client
        self._limit = limit
        self._window_ms = max(1, round(window_seconds * 1000))
        self._key_prefix = key_prefix
        self._script = client.register_script(_REDIS_INCR_WITH_WINDOW_EXPIRY)
        self._logger = get_logger("atp_api.rate_limit.redis")

    def allow(self, key: str) -> bool:
        redis_key = f"{self._key_prefix}:{key}"
        try:
            current = self._script(keys=[redis_key], args=[self._window_ms])
        except RedisError as exc:
            self._logger.warning(
                "rate_limiter_redis_unavailable_fail_open",
                key_prefix=self._key_prefix,
                error=type(exc).__name__,
            )
            _REDIS_UNAVAILABLE_TOTAL.labels(limiter=self._key_prefix).inc()
            return True
        return int(current) <= self._limit

    def close(self) -> None:
        """Release the underlying Redis connection(s). Not part of the
        `RateLimiter` Protocol (`InMemoryRateLimiter` has nothing to
        release) - `atp_api.app.create_app`'s lifespan calls this via
        `getattr(..., "close", None)` on whatever limiter it was given,
        rather than widening the Protocol for one implementation's
        cleanup need."""
        self._client.close()


def _default_key(scope: Scope) -> str:
    client = scope.get("client")
    host = client[0] if client else "unknown"
    return f"{host}:{scope.get('path', '')}"


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter,
        key_func: KeyFunc = _default_key,
    ) -> None:
        self._app = app
        self._limiter = limiter
        self._key_func = key_func

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        if not self._limiter.allow(self._key_func(scope)):
            # Local import: keeps this middleware's module free of a
            # framework-level import for the one response it needs to
            # construct outside FastAPI's own exception-handling path
            # (middleware runs outside the ASGI app FastAPI wraps with its
            # exception handlers, so raising ApiError here would not be
            # caught by atp_api.errors's handlers).
            from atp_api.errors import RateLimitExceededError
            from atp_api.schemas.common import ErrorResponse
            from atp_platform.correlation import get_correlation_id

            error = RateLimitExceededError()
            body = (
                ErrorResponse(
                    code=error.code, message=error.message, correlation_id=get_correlation_id()
                )
                .model_dump_json()
                .encode("utf-8")
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": error.status_code,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self._app(scope, receive, send)
