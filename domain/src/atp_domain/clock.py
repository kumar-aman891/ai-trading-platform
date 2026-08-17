"""Clock port: a UTC implementation for runtime use, and a deterministic
FrozenClock for tests.

Every timestamp-producing operation in this package takes a Clock rather
than calling `datetime.now()` directly - required for the deterministic
tests rules/05-testing.md mandates (market hours, session expiry, race
conditions are all untestable without clock injection).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class UTCClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("FrozenClock requires a timezone-aware datetime.")


@dataclass(slots=True)
class FrozenClock:
    """A mutable test double - `now()` returns whatever time was last set,
    advanced only by explicit `advance()`/`set()` calls, never by wall-clock
    passage."""

    _current: datetime

    def __post_init__(self) -> None:
        _require_aware(self._current)

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        self._current = self._current + delta

    def set(self, new_time: datetime) -> None:
        _require_aware(new_time)
        self._current = new_time
