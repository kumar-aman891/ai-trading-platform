"""Explicit domain <-> ORM mapping functions.

Nothing here is a domain type, and no domain dataclass ever gains an ORM
annotation (ADR-009, rules/01-architecture.md) - every conversion is a
plain function operating on the two independent types.

Known gap (documented, not silently papered over; reviewed in the Step 6
reconciliation - `paper.orders.intent_id` was resolved by widening the
Step 4 domain type, the rest were confirmed as deliberately outside it):
three columns required by docs/schemas/ have no corresponding field on
their Step 4 domain dataclass, because they are application/provenance
metadata (who did this, or which subsystem produced this row) rather than
business facts a trading decision needs to reason about:

- `paper.trade_proposals.created_by` (nullable FK to `core.users`, ADR-015) -
  `atp_domain.proposals.TradeProposal` does not track who created it; no
  risk rule consults it. The authenticated caller's identity is an
  API/auth-layer fact, not a domain one. `created_by` is `NULL` for a
  strategy-authored proposal (`strategy_id` carries attribution instead);
  the `proposal_has_an_author` CHECK (migration 0006) requires at least
  one of the two.
- `paper.fills.source` (NOT NULL) - `atp_domain.orders.Fill` already
  carries `simulated: bool`; `source` only names which mechanism produced
  the fill, which nothing in `Position.apply_fill` or any risk rule reads.
- `core.risk_config.active` / `.created_by` (`.config`, a generic JSON
  limits blob, is a separate, acknowledged Step 4 completeness gap -
  `atp_domain.risk.config.RiskConfig` only models `max_order_notional`
  today because Phase 1 only implements two capital/notional rules) -
  "which version is current" is a repository selection concern, and
  `created_by` is accountability metadata like the two fields above.
  `created_by` is nullable (`core.risk_config.created_by IS NULL` for the
  migration-seeded bootstrap row only - see
  `docs/schemas/risk_config.md` and `docs/schemas/user.md`'s "no seeded
  user, ever" rule, reconciled by mirroring
  `core.kill_switch_state.updated_by`'s existing NULL-for-system pattern).

`paper.orders.intent_id` is **not** in this list: it is the direct link in
the TradeProposal -> RiskDecision -> ApprovedOrderIntent -> Order chain
(ADR-008), so it now lives on `atp_domain.orders.Order` itself.
"""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType
from typing import cast

from atp_domain.audit import AuditEvent
from atp_domain.intents import ApprovedOrderIntent, CanonicalOrderPayload
from atp_domain.money import Money, Price, Quantity
from atp_domain.orders import Fill, Order, Position
from atp_domain.proposals import TradeProposal
from atp_domain.risk.config import RiskConfig
from atp_domain.risk.engine import RiskDecision
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.types import (
    ActorType,
    DecisionId,
    DecisionOutcome,
    EventId,
    FillId,
    InstrumentId,
    IntentId,
    Mode,
    OrderId,
    OrderStatus,
    OrderType,
    PositionId,
    Product,
    ProposalId,
    RiskConfigId,
    Side,
    SignalId,
    StrategyId,
)
from atp_persistence.models.audit import AuditEventRow
from atp_persistence.models.core import RiskConfigRow
from atp_persistence.models.paper import (
    FillRow,
    OrderIntentRow,
    OrderRow,
    PositionRow,
    RiskDecisionRow,
    TradeProposalRow,
)

# ---------------------------------------------------------------------------
# TradeProposal
# ---------------------------------------------------------------------------


def trade_proposal_to_row(proposal: TradeProposal, *, created_by: str | None) -> TradeProposalRow:
    return TradeProposalRow(
        proposal_id=proposal.proposal_id,
        mode=proposal.mode.value,
        instrument_id=proposal.instrument_id,
        side=proposal.side.value,
        quantity=proposal.quantity.value,
        order_type=proposal.order_type.value,
        limit_price=proposal.limit_price.value if proposal.limit_price else None,
        trigger_price=proposal.trigger_price.value if proposal.trigger_price else None,
        product=proposal.product.value,
        strategy_id=proposal.strategy_id,
        strategy_version=proposal.strategy_version,
        source_signal_id=proposal.source_signal_id,
        client_request_id=proposal.client_request_id,
        expected_risk=dict(proposal.expected_risk),
        created_by=created_by,
        created_at=proposal.created_at,
    )


def row_to_trade_proposal(row: TradeProposalRow) -> TradeProposal:
    return TradeProposal(
        proposal_id=ProposalId(row.proposal_id),
        mode=Mode(row.mode),
        instrument_id=InstrumentId(row.instrument_id),
        side=Side(row.side),
        quantity=Quantity(row.quantity),
        order_type=OrderType(row.order_type),
        limit_price=Price(row.limit_price) if row.limit_price is not None else None,
        trigger_price=Price(row.trigger_price) if row.trigger_price is not None else None,
        product=Product(row.product),
        client_request_id=row.client_request_id,
        created_at=row.created_at,
        strategy_id=StrategyId(row.strategy_id) if row.strategy_id is not None else None,
        strategy_version=row.strategy_version,
        source_signal_id=SignalId(row.source_signal_id)
        if row.source_signal_id is not None
        else None,
        expected_risk=MappingProxyType(dict(row.expected_risk)),
    )


# ---------------------------------------------------------------------------
# RiskDecision
# ---------------------------------------------------------------------------


def _rule_result_to_dict(result: RuleResult) -> dict[str, object]:
    return {
        "rule_id": result.rule_id,
        "outcome": result.outcome.value,
        "message": result.message,
        "evidence": dict(result.evidence),
    }


def _dict_to_rule_result(data: dict[str, object]) -> RuleResult:
    evidence_raw = data.get("evidence")
    evidence = (
        {str(k): str(v) for k, v in evidence_raw.items()} if isinstance(evidence_raw, dict) else {}
    )
    return RuleResult(
        rule_id=str(data["rule_id"]),
        outcome=RuleOutcome(str(data["outcome"])),
        message=str(data["message"]),
        evidence=evidence,
    )


def risk_decision_to_row(decision: RiskDecision) -> RiskDecisionRow:
    return RiskDecisionRow(
        decision_id=decision.decision_id,
        mode=decision.mode.value,
        proposal_id=decision.proposal_id,
        outcome=decision.outcome.value,
        rule_results=[_rule_result_to_dict(r) for r in decision.rule_results],
        risk_config_id=decision.risk_config_id,
        limit_snapshot_hash=decision.limit_snapshot_hash,
        decided_at=decision.decided_at,
    )


def row_to_risk_decision(row: RiskDecisionRow) -> RiskDecision:
    return RiskDecision(
        decision_id=DecisionId(row.decision_id),
        mode=Mode(row.mode),
        proposal_id=ProposalId(row.proposal_id),
        outcome=DecisionOutcome(row.outcome),
        rule_results=tuple(
            _dict_to_rule_result(cast("dict[str, object]", r)) for r in row.rule_results
        ),
        risk_config_id=RiskConfigId(row.risk_config_id),
        limit_snapshot_hash=row.limit_snapshot_hash,
        decided_at=row.decided_at,
    )


# ---------------------------------------------------------------------------
# ApprovedOrderIntent (write-only - see OrderIntentRepository's docstring
# for why no row_to_order_intent function exists)
# ---------------------------------------------------------------------------


def _canonical_payload_to_dict(payload: CanonicalOrderPayload) -> dict[str, object]:
    return {
        "instrument_id": payload.instrument_id,
        "side": payload.side.value,
        "quantity": str(payload.quantity.value),
        "order_type": payload.order_type.value,
        "limit_price": str(payload.limit_price.value) if payload.limit_price is not None else None,
        "trigger_price": str(payload.trigger_price.value)
        if payload.trigger_price is not None
        else None,
        "product": payload.product.value,
    }


def order_intent_to_row(intent: ApprovedOrderIntent) -> OrderIntentRow:
    return OrderIntentRow(
        intent_id=intent.intent_id,
        mode=intent.mode.value,
        decision_id=intent.decision_id,
        proposal_id=intent.proposal_id,
        canonical_payload=_canonical_payload_to_dict(intent.canonical_payload),
        payload_hash=intent.payload_hash,
        minted_at=intent.minted_at,
        expires_at=intent.expires_at,
    )


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


def order_to_row(order: Order) -> OrderRow:
    return OrderRow(
        internal_order_id=order.internal_order_id,
        mode=order.mode.value,
        broker_order_id=None,
        broker_provider=None,
        proposal_id=order.proposal_id,
        intent_id=order.intent_id,
        idempotency_key=order.idempotency_key,
        status=order.status.value,
        submitted_at=order.submitted_at,
        acknowledged_at=order.acknowledged_at,
        last_update_at=order.last_update_at,
    )


def row_to_order(row: OrderRow) -> Order:
    return Order(
        internal_order_id=OrderId(row.internal_order_id),
        mode=Mode(row.mode),
        proposal_id=ProposalId(row.proposal_id),
        intent_id=IntentId(row.intent_id),
        idempotency_key=row.idempotency_key,
        status=OrderStatus(row.status),
        submitted_at=row.submitted_at,
        acknowledged_at=row.acknowledged_at,
        last_update_at=row.last_update_at,
    )


# ---------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------


def fill_to_row(fill: Fill, *, source: str) -> FillRow:
    return FillRow(
        fill_id=fill.fill_id,
        mode=fill.mode.value,
        internal_order_id=fill.internal_order_id,
        broker_trade_id=None,
        quantity=fill.quantity.value,
        price=fill.price.value,
        fees=fill.fees.value,
        taxes=fill.taxes.value,
        simulated=fill.simulated,
        source=source,
        filled_at=fill.filled_at,
    )


def row_to_fill(row: FillRow) -> Fill:
    return Fill(
        fill_id=FillId(row.fill_id),
        mode=Mode(row.mode),
        internal_order_id=OrderId(row.internal_order_id),
        quantity=Quantity(row.quantity),
        price=Price(row.price),
        fees=Money(row.fees),
        taxes=Money(row.taxes),
        simulated=row.simulated,
        filled_at=row.filled_at,
    )


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


def position_to_row(position: Position) -> PositionRow:
    return PositionRow(
        position_id=position.position_id,
        mode=position.mode.value,
        instrument_id=position.instrument_id,
        quantity=position.quantity,
        average_price=position.average_price.value if position.average_price else None,
        realized_pnl=position.realized_pnl.value,
        unrealized_pnl=position.unrealized_pnl.value,
        updated_at=position.updated_at,
    )


def row_to_position(row: PositionRow) -> Position:
    return Position(
        position_id=PositionId(row.position_id),
        instrument_id=InstrumentId(row.instrument_id),
        mode=Mode(row.mode),
        quantity=row.quantity,
        average_price=Price(row.average_price) if row.average_price is not None else None,
        realized_pnl=Money(row.realized_pnl),
        unrealized_pnl=Money(row.unrealized_pnl),
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# RiskConfig
# ---------------------------------------------------------------------------


def risk_config_to_row(
    config: RiskConfig, *, active: bool, created_by: str | None = None
) -> RiskConfigRow:
    """`created_by=None` is valid only for the migration-seeded bootstrap
    row (docs/schemas/risk_config.md) - every application-driven version
    change must supply a real administrator's `user_id`. Nothing in this
    function enforces that distinction; it is a caller responsibility, the
    same way `core.kill_switch_state.updated_by`'s NULL-for-system
    convention is caller-enforced, not type-enforced."""
    return RiskConfigRow(
        risk_config_id=config.risk_config_id,
        mode=config.mode.value,
        version=config.version,
        config={"max_order_notional": str(config.max_order_notional.value)},
        config_hash=config.config_hash,
        active=active,
        created_at=config.created_at,
        created_by=created_by,
    )


def row_to_risk_config(row: RiskConfigRow) -> RiskConfig:
    return RiskConfig(
        risk_config_id=RiskConfigId(row.risk_config_id),
        mode=Mode(row.mode),
        version=row.version,
        max_order_notional=Money(Decimal(str(row.config["max_order_notional"]))),
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# AuditEvent
# ---------------------------------------------------------------------------


def audit_event_to_row(event: AuditEvent) -> AuditEventRow:
    return AuditEventRow(
        event_id=event.event_id,
        correlation_id=event.correlation_id,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        actor_type=event.actor_type.value,
        actor_id=event.actor_id,
        action=event.action,
        mode=event.mode.value if event.mode is not None else None,
        strategy_id=event.strategy_id,
        strategy_version=event.strategy_version,
        instrument_id=event.instrument_id,
        source_refs=dict(event.source_refs) if event.source_refs else None,
        input_hash=event.input_hash,
        decision=event.decision,
        risk_rule_ids=list(event.risk_rule_ids) if event.risk_rule_ids else None,
        broker_order_id=event.broker_order_id,
        broker_provider=event.broker_provider,
        error_code=event.error_code,
        error_class=event.error_class,
        payload=None,
    )


def row_to_audit_event(row: AuditEventRow) -> AuditEvent:
    return AuditEvent(
        event_id=EventId(row.event_id),
        correlation_id=row.correlation_id,
        occurred_at=row.occurred_at,
        recorded_at=row.recorded_at,
        actor_type=ActorType(row.actor_type),
        actor_id=row.actor_id,
        action=row.action,
        mode=Mode(row.mode) if row.mode is not None else None,
        strategy_id=StrategyId(row.strategy_id) if row.strategy_id is not None else None,
        strategy_version=row.strategy_version,
        instrument_id=InstrumentId(row.instrument_id) if row.instrument_id is not None else None,
        source_refs=MappingProxyType({str(k): str(v) for k, v in row.source_refs.items()})
        if row.source_refs
        else MappingProxyType({}),
        input_hash=row.input_hash,
        decision=row.decision,
        risk_rule_ids=tuple(row.risk_rule_ids) if row.risk_rule_ids else (),
        broker_order_id=row.broker_order_id,
        broker_provider=row.broker_provider,
        error_code=row.error_code,
        error_class=row.error_class,
    )
