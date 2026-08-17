"""Tests for atp_domain.audit.AuditEvent - immutability and field
validation."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from atp_domain.audit import ACTION_PROPOSAL_CREATED, AuditEvent
from atp_domain.types import ActorType, EventId, Mode

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _event(**overrides: object) -> AuditEvent:
    defaults: dict[str, object] = {
        "event_id": EventId("ffffffff-ffff-7fff-8fff-ffffffffffff"),
        "correlation_id": "corr-1",
        "occurred_at": NOW,
        "recorded_at": NOW,
        "actor_type": ActorType.SYSTEM,
        "actor_id": None,
        "action": ACTION_PROPOSAL_CREATED,
        "mode": Mode.PAPER,
        "strategy_id": None,
        "strategy_version": None,
        "instrument_id": None,
    }
    defaults.update(overrides)
    return AuditEvent(**defaults)  # type: ignore[arg-type]


def test_valid_event_constructs() -> None:
    event = _event()
    assert event.action == ACTION_PROPOSAL_CREATED


def test_event_is_immutable() -> None:
    event = _event()
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.action = "SOMETHING_ELSE"  # type: ignore[misc]


def test_naive_occurred_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="occurred_at"):
        _event(occurred_at=datetime(2026, 1, 1))


def test_naive_recorded_at_is_rejected() -> None:
    with pytest.raises(ValueError, match="recorded_at"):
        _event(recorded_at=datetime(2026, 1, 1))


def test_empty_action_is_rejected() -> None:
    with pytest.raises(ValueError, match="action"):
        _event(action="   ")


def test_broker_order_id_and_provider_must_be_paired() -> None:
    with pytest.raises(ValueError, match="together"):
        _event(broker_order_id="XYZ123", broker_provider=None)
    with pytest.raises(ValueError, match="together"):
        _event(broker_order_id=None, broker_provider="KITE")


def test_broker_order_id_and_provider_together_is_valid() -> None:
    event = _event(broker_order_id="XYZ123", broker_provider="KITE")
    assert event.broker_order_id == "XYZ123"


def test_mode_may_be_none_for_mode_agnostic_events() -> None:
    event = _event(mode=None, action="LOGIN_SUCCEEDED")
    assert event.mode is None
