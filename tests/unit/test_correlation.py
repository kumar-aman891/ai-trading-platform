"""Tests for atp_platform.correlation — contextvar-based correlation ID
propagation."""

from __future__ import annotations

from atp_platform import correlation


def test_correlation_id_is_created_when_absent() -> None:
    assert correlation.get_correlation_id() is None

    with correlation.correlation_scope() as cid:
        assert cid is not None
        assert correlation.get_correlation_id() == cid

    assert correlation.get_correlation_id() is None


def test_supplied_correlation_id_is_preserved() -> None:
    with correlation.correlation_scope("fixed-id-123") as cid:
        assert cid == "fixed-id-123"
        assert correlation.get_correlation_id() == "fixed-id-123"


def test_new_correlation_id_generates_unique_values() -> None:
    first = correlation.new_correlation_id()
    second = correlation.new_correlation_id()

    assert first != second


def test_manual_set_and_reset_round_trips() -> None:
    assert correlation.get_correlation_id() is None

    token = correlation.set_correlation_id("manual-id")
    try:
        assert correlation.get_correlation_id() == "manual-id"
    finally:
        correlation.reset_correlation_id(token)

    assert correlation.get_correlation_id() is None


def test_nested_scopes_restore_the_outer_value() -> None:
    with correlation.correlation_scope("outer") as outer_id:
        assert correlation.get_correlation_id() == outer_id
        with correlation.correlation_scope("inner") as inner_id:
            assert correlation.get_correlation_id() == inner_id
        assert correlation.get_correlation_id() == outer_id
