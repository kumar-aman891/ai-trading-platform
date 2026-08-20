"""Tests for atp_platform.metrics — the registry and its three factory
primitives (`counter`/`histogram`/`gauge`). Concrete service metrics
(`atp_worker`'s job-outcome counter and session-reap gauge, `atp_api`'s
HTTP counter/histogram) are tested at their own call sites; this file
covers the factories in isolation, each against a locally-scoped test
metric name so it never collides with a real one registered elsewhere
in the same test process (`PLATFORM_REGISTRY` is a genuine process-wide
singleton, not reset between tests)."""

from __future__ import annotations

from atp_platform.metrics import PLATFORM_REGISTRY, counter, gauge, get_registry, histogram


def test_get_registry_returns_the_shared_platform_registry() -> None:
    assert get_registry() is PLATFORM_REGISTRY


def test_counter_is_registered_on_the_platform_registry_and_incrementable() -> None:
    metric = counter("test_config_requests", "test counter", labelnames=("outcome",))

    metric.labels(outcome="ok").inc()

    value = PLATFORM_REGISTRY.get_sample_value("test_config_requests_total", {"outcome": "ok"})
    assert value == 1.0


def test_histogram_is_registered_on_the_platform_registry_and_observable() -> None:
    metric = histogram("test_config_latency_seconds", "test histogram")

    metric.observe(0.5)

    count = PLATFORM_REGISTRY.get_sample_value("test_config_latency_seconds_count")
    assert count == 1.0


def test_gauge_is_registered_on_the_platform_registry_and_settable() -> None:
    metric = gauge("test_config_expired_count", "test gauge")

    metric.set(3)

    value = PLATFORM_REGISTRY.get_sample_value("test_config_expired_count")
    assert value == 3.0


def test_gauge_overwrites_rather_than_accumulates() -> None:
    """The property that distinguishes a Gauge from a Counter - a second
    `set()` replaces the value, it does not add to it. This is what makes
    `SESSION_REAP`'s gauge (ADR-013 Section 2) correctly report zero on a
    run that observes nothing, rather than a monotonic total that could
    never decrease."""
    metric = gauge("test_config_overwrite_count", "test gauge")

    metric.set(5)
    metric.set(2)

    value = PLATFORM_REGISTRY.get_sample_value("test_config_overwrite_count")
    assert value == 2.0
