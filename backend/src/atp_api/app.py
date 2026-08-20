"""The deterministic FastAPI application factory (Phase 1 Step 7).

`create_app()` performs no trading action, no broker call, and starts no
scheduler - it only wires configuration, an optional database engine, and
routes. Every dependency (`Settings`, `ApiSettings`, the session factory,
the rate limiter) is either passed in explicitly or built from an
explicitly passed-in `Settings.database_url` - there is no module-level
global engine or session anywhere in this package.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from atp_api.config import ApiSettings, load_api_settings
from atp_api.errors import register_exception_handlers
from atp_api.middleware.rate_limit import InMemoryRateLimiter, RateLimiter, RateLimitMiddleware
from atp_api.middleware.request_logging import RequestLoggingMiddleware
from atp_api.middleware.security_headers import SecurityHeadersMiddleware
from atp_api.openapi import API_DESCRIPTION, API_TITLE, API_VERSION, OPENAPI_TAGS
from atp_api.routers import audit, auth, health, instruments, kill_switches, metrics, paper, system
from atp_persistence.db import create_engine, make_session_factory
from atp_platform.asgi import CorrelationIdMiddleware
from atp_platform.config import Settings


def create_app(
    *,
    settings: Settings,
    api_settings: ApiSettings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    rate_limiter: RateLimiter | None = None,
    login_rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Build a fully wired, ready-to-serve FastAPI app.

    `session_factory=None` (the default) builds one from
    `settings.database_url` via `atp_persistence.db.create_engine` -
    `create_async_engine` never opens a connection at construction time
    (SQLAlchemy is lazy about that), so calling this with an unreachable
    DSN is safe and exactly what dependency-failure tests use (no Docker
    required to prove `/readyz`, `/api/v1/audit/events`, etc. fail closed).
    Pass an explicit `session_factory` to point at a real or fake
    database instead.

    `rate_limiter`/`login_rate_limiter=None` (the default) each build an
    `InMemoryRateLimiter` - the correct choice for a single-process
    deployment or a Docker-free test, and what every test in
    `tests/unit/api/` still relies on. `atp_api.main` (the one real
    process entrypoint) passes an explicit `RedisRateLimiter` for each
    instead, so the deployed, potentially multi-process service shares one
    budget per key across processes (`atp_api.middleware.rate_limit`'s own
    module docstring). Either limiter passed in is closed (if it exposes a
    `close()`) when the app's lifespan ends.

    `api_settings=None` builds one via `load_api_settings()`, which
    validates the environment (CORS wildcard+credentials, etc.) and raises
    on invalid configuration - this is what makes "the application must
    fail startup if required configuration is invalid" true for the
    API-specific settings, mirroring `Settings`' own fail-fast validation
    for the platform-wide ones.
    """
    resolved_api_settings = api_settings if api_settings is not None else load_api_settings()

    engine: AsyncEngine | None = None
    resolved_session_factory = session_factory
    if resolved_session_factory is None:
        engine = create_engine(settings.database_url.get_secret_value())
        resolved_session_factory = make_session_factory(engine)

    limiter = rate_limiter or InMemoryRateLimiter(
        limit=resolved_api_settings.rate_limit_requests,
        window_seconds=resolved_api_settings.rate_limit_window_seconds,
    )
    # A separate, stricter limiter dedicated to POST /api/v1/auth/login
    # (`atp_api.deps.enforce_login_rate_limit`) - independent bucket from
    # the general per-path limiter above.
    resolved_login_rate_limiter = login_rate_limiter or InMemoryRateLimiter(
        limit=resolved_api_settings.login_rate_limit_requests,
        window_seconds=resolved_api_settings.login_rate_limit_window_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()
            # `close()` is not part of the `RateLimiter` Protocol (only
            # `RedisRateLimiter` has a connection to release) - duck-typed
            # rather than widening the Protocol for one implementation.
            for candidate_limiter in (limiter, resolved_login_rate_limiter):
                close = getattr(candidate_limiter, "close", None)
                if callable(close):
                    close()

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.api_settings = resolved_api_settings
    app.state.session_factory = resolved_session_factory
    app.state.engine = engine

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(metrics.router)
    app.include_router(system.router)
    app.include_router(kill_switches.router)
    app.include_router(audit.router)
    app.include_router(auth.router)
    app.include_router(instruments.router)
    app.include_router(paper.router)

    app.state.login_rate_limiter = resolved_login_rate_limiter

    # Registration order matters: Starlette makes the *last*-registered
    # middleware the *outermost* ASGI layer (it runs first on the way in,
    # last on the way out). CorrelationIdMiddleware is registered last so
    # every other middleware, every exception handler, and every log line
    # sees a correlation ID already bound to the current context.
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_api_settings.cors_allowed_origins),
        allow_credentials=resolved_api_settings.cors_allow_credentials,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts_enabled=settings.environment == "production")
    app.add_middleware(CorrelationIdMiddleware)

    return app
