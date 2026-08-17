"""Tests for atp_domain.clock - UTCClock and the deterministic FrozenClock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atp_domain.clock import Clock, FrozenClock, UTCClock


def test_utc_clock_returns_timezone_aware_datetime() -> None:
    value = UTCClock().now()
    assert value.tzinfo is not None


def test_utc_clock_satisfies_clock_protocol() -> None:
    assert isinstance(UTCClock(), Clock)


def test_frozen_clock_returns_fixed_time_until_advanced() -> None:
    fixed = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FrozenClock(fixed)

    assert clock.now() == fixed
    assert clock.now() == fixed  # calling twice does not advance it


def test_frozen_clock_advance() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    clock.advance(timedelta(hours=1))
    assert clock.now() == datetime(2026, 1, 1, 1, 0, tzinfo=UTC)


def test_frozen_clock_set() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    clock.set(datetime(2030, 6, 1, tzinfo=UTC))
    assert clock.now() == datetime(2030, 6, 1, tzinfo=UTC)


def test_frozen_clock_rejects_naive_datetime_at_construction() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 1, 1))


def test_frozen_clock_rejects_naive_datetime_on_set() -> None:
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2026, 1, 1))


def test_frozen_clock_satisfies_clock_protocol() -> None:
    assert isinstance(FrozenClock(datetime(2026, 1, 1, tzinfo=UTC)), Clock)
