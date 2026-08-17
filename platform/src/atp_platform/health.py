"""Health/readiness probe primitives.

`liveness()` answers "is the process up" and never depends on anything
external. `readiness()` aggregates a caller-supplied set of dependency
checks and fails closed: any check that reports FAIL, or raises instead of
returning, makes the whole probe FAIL. No concrete dependency checks are
registered here - no persistence layer exists yet (Phase 1 Step 8). The
real DB/Redis/kill-switch-readable checks are registered by the service
that owns them, in later steps, using this aggregator.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum


class ProbeStatus(StrEnum):
    OK = "OK"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    status: ProbeStatus
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is ProbeStatus.OK


Check = Callable[[], ProbeResult]


def liveness() -> ProbeResult:
    """The process is up and able to answer. No dependency is consulted."""
    return ProbeResult(name="liveness", status=ProbeStatus.OK)


def readiness(checks: Iterable[Check]) -> ProbeResult:
    """Aggregate dependency checks. Deterministic: the result depends only
    on what each check returns (or raises), never on timing or ordering.

    Fails closed - a check that raises is treated as a failure, not
    ignored, per the platform-wide "reject rather than guess" policy
    (.claude/rules/02-live-trading.md's principle applied to readiness).
    """
    failures: list[str] = []
    for check in checks:
        try:
            result = check()
        except Exception as exc:  # deliberately broad: any failure here means FAIL, not a crash
            failures.append(f"{check!r} raised {exc.__class__.__name__}: {exc}")
            continue
        if not result.ok:
            failures.append(f"{result.name}: {result.detail or 'FAIL'}")

    if failures:
        return ProbeResult(name="readiness", status=ProbeStatus.FAIL, detail="; ".join(failures))
    return ProbeResult(name="readiness", status=ProbeStatus.OK)
