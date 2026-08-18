"""Async dependency-health checks, reusing `atp_platform.health`'s
`ProbeResult`/`ProbeStatus` types.

`atp_platform.health.readiness()` takes *synchronous* check callables
(`Callable[[], ProbeResult]`); Step 7's one real dependency check (database
connectivity) is inherently async (`AsyncSession`). Rather than force a
sync round trip (which would require a nested event loop inside a running
one - not possible) or modify the shared platform primitive to fit one
caller, `async_readiness` below is a small async-native counterpart with
the identical fail-closed contract: a check that raises, or reports FAIL,
makes the aggregate FAIL. Every detail returned here is safe to log; it is
`atp_api.routers.health`'s job to decide what subset (if any) is safe to
put in an HTTP response.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atp_persistence.db import read_only_session
from atp_platform.health import ProbeResult, ProbeStatus

AsyncCheck = Callable[[], Awaitable[ProbeResult]]


async def async_readiness(checks: Sequence[AsyncCheck]) -> tuple[ProbeResult, list[ProbeResult]]:
    """Returns `(aggregate, per_check_results)`. `aggregate.detail` is
    intentionally never more specific than a list of check names - never
    an exception message, which could contain a DSN/hostname."""
    results: list[ProbeResult] = []
    failed_names: list[str] = []

    for check in checks:
        try:
            result = await check()
        except Exception:
            result = ProbeResult(name="dependency", status=ProbeStatus.FAIL)
        results.append(result)
        if not result.ok:
            failed_names.append(result.name)

    if failed_names:
        aggregate = ProbeResult(
            name="readiness", status=ProbeStatus.FAIL, detail=", ".join(failed_names)
        )
    else:
        aggregate = ProbeResult(name="readiness", status=ProbeStatus.OK)
    return aggregate, results


async def database_check(
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> ProbeResult:
    """`detail` is the exception *class name* only (e.g. "OperationalError")
    - never `str(exc)`, which for a connection failure typically embeds the
    DSN, host, and sometimes the role name (security/SECRET_HANDLING.md)."""
    if session_factory is None:
        return ProbeResult(name="database", status=ProbeStatus.FAIL, detail="not_configured")
    try:
        async with read_only_session(session_factory) as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        return ProbeResult(name="database", status=ProbeStatus.FAIL, detail=type(exc).__name__)
    return ProbeResult(name="database", status=ProbeStatus.OK)
