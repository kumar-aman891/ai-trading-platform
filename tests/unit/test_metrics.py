"""Tests for atp_platform.metrics — registry primitives (no concrete
service metrics exist yet; see module docstring)."""

from __future__ import annotations

from atp_platform.metrics import PLATFORM_REGISTRY, counter, get_registry, histogram


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
