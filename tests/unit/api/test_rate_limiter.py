"""RATE LIMITER: the `RateLimiter` abstraction, the deterministic
in-memory implementation, and its wiring as ASGI middleware."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from atp_api.app import create_app
from atp_api.middleware import rate_limit as rate_limit_module
from atp_api.middleware.rate_limit import InMemoryRateLimiter, RateLimiter
from atp_domain.clock import FrozenClock
from atp_platform.config import Settings


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
