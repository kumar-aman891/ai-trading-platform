"""Prometheus metric registry and primitives.

A dedicated registry, not `prometheus_client.REGISTRY` (the library's
process-global default) - this avoids duplicate-registration collisions
across test runs and keeps this platform's metrics namespace explicit
rather than an ambient global. No concrete metrics are defined here: no
Phase 1 service emits any yet (the `/metrics` route and the first real
counters are Phase 1 Step 13+). These are factory primitives only.
"""

from __future__ import annotations

from collections.abc import Sequence

from prometheus_client import CollectorRegistry, Counter, Histogram

PLATFORM_REGISTRY: CollectorRegistry = CollectorRegistry()


def get_registry() -> CollectorRegistry:
    return PLATFORM_REGISTRY


def counter(name: str, documentation: str, labelnames: Sequence[str] = ()) -> Counter:
    """Create a Counter registered on the platform registry."""
    return Counter(name, documentation, labelnames=labelnames, registry=PLATFORM_REGISTRY)


def histogram(name: str, documentation: str, labelnames: Sequence[str] = ()) -> Histogram:
    """Create a Histogram registered on the platform registry."""
    return Histogram(name, documentation, labelnames=labelnames, registry=PLATFORM_REGISTRY)
