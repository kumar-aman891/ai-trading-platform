"""AuditEvent - immutable domain artifact, fields aligned with
docs/OBSERVABILITY.md and docs/schemas/audit_event.md.

Persistence (the append-only `audit.audit_events` table, its triggers and
grants) is not implemented here - this module defines the domain-level
shape only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from atp_domain.types import ActorType, EventId, InstrumentId, Mode, StrategyId

# Known Phase 1 actions (docs/schemas/audit_event.md's examples). `action`
# itself stays a plain `str` field, not a closed enum - the vocabulary is
# meant to grow as new modules land without requiring a domain change.
ACTION_PROPOSAL_CREATED = "PROPOSAL_CREATED"
ACTION_RISK_DECISION_RECORDED = "RISK_DECISION_RECORDED"
ACTION_INTENT_MINTED = "INTENT_MINTED"
ACTION_ORDER_SUBMITTED = "ORDER_SUBMITTED"
ACTION_KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
ACTION_KILL_SWITCH_DISENGAGED = "KILL_SWITCH_DISENGAGED"
ACTION_LOGIN_SUCCEEDED = "LOGIN_SUCCEEDED"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: EventId
    correlation_id: str
    occurred_at: datetime
    recorded_at: datetime
    actor_type: ActorType
    actor_id: str | None
    action: str
    mode: Mode | None
    strategy_id: StrategyId | None
    strategy_version: int | None
    instrument_id: InstrumentId | None
    source_refs: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    input_hash: str | None = None
    decision: str | None = None
    risk_rule_ids: tuple[str, ...] = ()
    broker_order_id: str | None = None
    broker_provider: str | None = None
    error_code: str | None = None
    error_class: str | None = None

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware.")
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware.")
        if not self.action.strip():
            raise ValueError("action must not be empty.")
        if (self.broker_order_id is None) != (self.broker_provider is None):
            raise ValueError(
                "broker_order_id and broker_provider must be supplied together or not at all."
            )
        if not isinstance(self.source_refs, MappingProxyType):
            object.__setattr__(self, "source_refs", MappingProxyType(dict(self.source_refs)))
