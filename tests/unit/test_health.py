"""Tests for atp_platform.health — liveness/readiness primitives."""

from __future__ import annotations

from atp_platform.health import ProbeResult, ProbeStatus, liveness, readiness


def test_liveness_reports_ok() -> None:
    result = liveness()

    assert result.status is ProbeStatus.OK
    assert result.ok is True


def _ok_check() -> ProbeResult:
    return ProbeResult(name="dep-a", status=ProbeStatus.OK)


def _fail_check() -> ProbeResult:
    return ProbeResult(name="dep-b", status=ProbeStatus.FAIL, detail="unreachable")


def _raising_check() -> ProbeResult:
    raise RuntimeError("boom")


def test_readiness_is_ok_when_every_check_passes() -> None:
    result = readiness([_ok_check, _ok_check])

    assert result.status is ProbeStatus.OK
    assert result.ok is True


def test_readiness_fails_closed_when_a_check_reports_fail() -> None:
    result = readiness([_ok_check, _fail_check])

    assert result.status is ProbeStatus.FAIL
    assert "dep-b" in (result.detail or "")


def test_readiness_fails_closed_when_a_check_raises() -> None:
    result = readiness([_ok_check, _raising_check])

    assert result.status is ProbeStatus.FAIL
    assert "RuntimeError" in (result.detail or "")


def test_readiness_evaluation_is_deterministic_given_the_same_checks() -> None:
    checks = [_ok_check, _fail_check]

    first = readiness(checks)
    second = readiness(checks)

    assert first == second
