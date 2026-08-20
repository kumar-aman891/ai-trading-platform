"""METRICS: `GET /metrics` (Phase 1 Step 13, observability foundation).

Mirrors `test_health.py`'s style - a bare `TestClient(create_app(...))`,
no need for the fully-wired `app`/`client` fixtures in `conftest.py`,
since this route touches no repository. Metric-value assertions use a
before/after delta on the shared, process-wide `PLATFORM_REGISTRY`
(`tests/unit/test_metrics.py`'s own module docstring explains why), never
an absolute value - this file's own two `TestClient` requests are not the
only traffic `atp_api_http_requests_total` will ever see in the same test
process.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_platform.config import Settings
from atp_platform.metrics import PLATFORM_REGISTRY


def test_metrics_route_returns_200_with_the_prometheus_content_type(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_metrics_route_exposes_the_http_metric_names(settings: Settings) -> None:
    """Module-level metric objects exist from import time, before any
    request has ever incremented them - `atp_api.middleware.
    request_logging` is imported as part of `create_app()` itself, so
    their `# HELP`/`# TYPE` lines appear in the body regardless of
    whether this specific test process has made any other HTTP request
    yet."""
    client = TestClient(create_app(settings=settings))

    body = client.get("/metrics").text

    assert "atp_api_http_requests_total" in body
    assert "atp_api_http_request_duration_seconds" in body


def test_a_request_through_the_app_increments_the_http_request_counter(
    settings: Settings,
) -> None:
    client = TestClient(create_app(settings=settings))
    before = (
        PLATFORM_REGISTRY.get_sample_value(
            "atp_api_http_requests_total", {"method": "GET", "status_code": "200"}
        )
        or 0.0
    )

    response = client.get("/healthz")
    assert response.status_code == 200

    after = PLATFORM_REGISTRY.get_sample_value(
        "atp_api_http_requests_total", {"method": "GET", "status_code": "200"}
    )
    assert after - before == 1.0


def test_a_request_through_the_app_observes_the_duration_histogram(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings))
    before = (
        PLATFORM_REGISTRY.get_sample_value(
            "atp_api_http_request_duration_seconds_count", {"method": "GET"}
        )
        or 0.0
    )

    client.get("/healthz")

    after = PLATFORM_REGISTRY.get_sample_value(
        "atp_api_http_request_duration_seconds_count", {"method": "GET"}
    )
    assert after - before == 1.0


def test_metrics_route_itself_does_not_recurse_into_its_own_metric(settings: Settings) -> None:
    """`GET /metrics` is still an HTTP request, so it moves the same
    `atp_api_http_requests_total` counter every other route does (no
    special-casing) - proven by delta, and phrased as its own test so a
    future attempt to exclude `/metrics` from instrumentation (which
    would be a silent, surprising behavior change) fails a named test
    rather than nothing."""
    client = TestClient(create_app(settings=settings))
    before = (
        PLATFORM_REGISTRY.get_sample_value(
            "atp_api_http_requests_total", {"method": "GET", "status_code": "200"}
        )
        or 0.0
    )

    client.get("/metrics")

    after = PLATFORM_REGISTRY.get_sample_value(
        "atp_api_http_requests_total", {"method": "GET", "status_code": "200"}
    )
    assert after - before == 1.0
