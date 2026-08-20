"""Prometheus metric registry and primitives.

A dedicated registry, not `prometheus_client.REGISTRY` (the library's
process-global default) - this avoids duplicate-registration collisions
across test runs and keeps this platform's metrics namespace explicit
rather than an ambient global. `atp_api.routers.metrics` (`GET /metrics`)
is the sole route that reads this registry; the first concrete metrics -
`atp_worker`'s job-outcome counter and `SESSION_REAP` gauge,
`atp_api`'s HTTP request counter/histogram - are defined at their own
call sites (`atp_worker.runner`, `atp_worker.handlers.session_expiry`,
`atp_api.middleware.request_logging`), not here. This module still
provides factory primitives only, one per Prometheus metric type this
codebase currently uses.

Every metric object returned by `counter()`/`gauge()`/`histogram()` must
be created exactly once, at module import time, and reused - calling a
factory function twice with the same `name` raises `ValueError` (a
`CollectorRegistry` rejects a duplicate registration), which is
`prometheus_client`'s own guard against exactly the double-definition
bug a per-request or per-call construction would invite.
"""

from __future__ import annotations

from collections.abc import Sequence

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

PLATFORM_REGISTRY: CollectorRegistry = CollectorRegistry()


def get_registry() -> CollectorRegistry:
    return PLATFORM_REGISTRY


def counter(name: str, documentation: str, labelnames: Sequence[str] = ()) -> Counter:
    """Create a Counter registered on the platform registry."""
    return Counter(name, documentation, labelnames=labelnames, registry=PLATFORM_REGISTRY)


def histogram(name: str, documentation: str, labelnames: Sequence[str] = ()) -> Histogram:
    """Create a Histogram registered on the platform registry."""
    return Histogram(name, documentation, labelnames=labelnames, registry=PLATFORM_REGISTRY)


def gauge(name: str, documentation: str, labelnames: Sequence[str] = ()) -> Gauge:
    """Create a Gauge registered on the platform registry - for a value
    that goes up or down (a point-in-time observation), unlike `counter()`,
    which only ever increases."""
    return Gauge(name, documentation, labelnames=labelnames, registry=PLATFORM_REGISTRY)
