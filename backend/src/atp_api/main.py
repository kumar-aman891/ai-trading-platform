"""Process entrypoint: `uvicorn atp_api.main:app`, or `python -m atp_api.main`
for local development.

The single, visible construction site for the process-wide `app` object -
`atp_api.app.create_app()` itself never runs at import time anywhere else
in this package (no other module constructs a `Settings`, an engine, or an
`app` at module scope), so this is the only place a "hidden global" could
even arise, and it does not: every value `create_app` receives is built
right here, explicitly, from `load_settings()`/`load_api_settings()`.

This is also the one place `RedisRateLimiter` is constructed (Phase 1 Step
15). `create_app()`'s own default (`InMemoryRateLimiter`) stays
per-process, which is wrong for the real deployed service - this module,
not `create_app`, is what makes the distributed limiter the one actually
running, by building it here from `settings.redis_url` (the same DSN
`atp_platform.config.Settings` already validates for every other Redis
use) and passing it in through `create_app`'s existing `rate_limiter`/
`login_rate_limiter` parameters.
"""

from __future__ import annotations

import os

import redis

from atp_api.app import create_app
from atp_api.config import load_api_settings
from atp_api.middleware.rate_limit import REDIS_SOCKET_TIMEOUT_SECONDS, RedisRateLimiter
from atp_platform.config import load_settings
from atp_platform.logging import configure_logging

settings = load_settings()
configure_logging(service="atp-api", level=settings.log_level)

api_settings = load_api_settings()

_redis_client = redis.Redis.from_url(
    settings.redis_url.get_secret_value(),
    socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
    socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
)

app = create_app(
    settings=settings,
    api_settings=api_settings,
    rate_limiter=RedisRateLimiter(
        client=_redis_client,
        limit=api_settings.rate_limit_requests,
        window_seconds=api_settings.rate_limit_window_seconds,
        key_prefix="ratelimit:general",
    ),
    login_rate_limiter=RedisRateLimiter(
        client=_redis_client,
        limit=api_settings.login_rate_limit_requests,
        window_seconds=api_settings.login_rate_limit_window_seconds,
        key_prefix="ratelimit:login",
    ),
)


def run() -> None:
    import uvicorn

    host = os.environ.get("ATP_API_HOST", "127.0.0.1")
    port = int(os.environ.get("ATP_API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
