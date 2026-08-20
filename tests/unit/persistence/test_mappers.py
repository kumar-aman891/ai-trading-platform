"""Domain -> ORM -> domain round-trip tests (Phase 1 Step 6).

No database connection is used anywhere in this module - ORM model
instances are constructed directly in memory (never flushed/queried), so
these tests run in every environment, Docker or not. What they prove is
narrower and more important than "the DB accepts this row": that mapping
through the ORM shape and back is lossless for the values rules/05-testing.md
and CLAUDE.md rule #10 care about most - Decimal precision, timezone-aware
timestamps, typed IDs, and enum values.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from atp_domain.audit import AuditEvent
from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_domain.intents import CanonicalOrderPayload
from atp_domain.money import Money, Price, Quantity
from atp_domain.orders import Fill, Order, Position
from atp_domain.proposals import TradeProposal
from atp_domain.risk.config import RiskConfig

# A RiskDecision requires atp_domain.risk.engine to construct legitimately
# (it validates outcome against rule_results in __post_init__), so these
# tests build one through the real module rather than hand-rolling a
# dataclass that could drift from the invariant it enforces.
from atp_domain.risk.engine import RiskDecision, mint_intent_for_decision
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
    StrategyId,
)
from atp_persistence import mappers

_AWARE_TS = datetime(2026, 1, 15, 9, 30, 0, tzinfo=UTC)


def test_trade_proposal_round_trip_preserves_decimal_and_timestamps() -> None:
    proposal = TradeProposal(
        proposal_id=ProposalId("11111111-1111-7111-8111-111111111111"),
        mode=Mode.PAPER,
        instrument_id=InstrumentId("22222222-2222-7222-8222-222222222222"),
        side=Side.BUY,
        quantity=Quantity(Decimal("10.500000")),
        order_type=OrderType.LIMIT,
        limit_price=Price(Decimal("1234.567890")),
        trigger_price=None,
        product=Product.CNC,
        client_request_id="req-1",
        created_at=_AWARE_TS,
        expected_risk=MappingProxyType({"note": "test"}),
    )

    row = mappers.trade_proposal_to_row(proposal, created_by="33333333-3333-7333-8333-333333333333")
    round_tripped = mappers.row_to_trade_proposal(row)

    assert round_tripped == proposal
    assert round_tripped.quantity.value == Decimal("10.500000")
    assert round_tripped.limit_price is not None
    assert round_tripped.limit_price.value == Decimal("1234.567890")
    assert round_tripped.created_at.tzinfo is not None
    assert round_tripped.mode is Mode.PAPER


def test_trade_proposal_to_row_accepts_none_created_by_for_a_strategy_authored_proposal() -> None:
    """ADR-015: created_by=None is the strategy-authored shape - the
    database's proposal_has_an_author CHECK (migration 0006), not this
    mapper, is what enforces strategy_id is present whenever created_by
    isn't; this test only proves the mapper itself accepts None without
    raising and round-trips cleanly."""
    proposal = TradeProposal(
        proposal_id=ProposalId("11111111-1111-7111-8111-111111111111"),
        mode=Mode.PAPER,
        instrument_id=InstrumentId("22222222-2222-7222-8222-222222222222"),
        side=Side.BUY,
        quantity=Quantity(Decimal("1")),
        order_type=OrderType.MARKET,
        limit_price=None,
        trigger_price=None,
        product=Product.CNC,
        client_request_id="strategy-req-1",
        created_at=_AWARE_TS,
        strategy_id=StrategyId("66666666-6666-7666-8666-666666666666"),
        strategy_version=1,
    )

    row = mappers.trade_proposal_to_row(proposal, created_by=None)

    assert row.created_by is None
    assert row.strategy_id == "66666666-6666-7666-8666-666666666666"
    round_tripped = mappers.row_to_trade_proposal(row)
    assert round_tripped == proposal


def test_risk_decision_round_trip_preserves_rule_results() -> None:
    import atp_domain.risk.engine as engine

    decision = RiskDecision(
        decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
        mode=Mode.PAPER,
        proposal_id=ProposalId("11111111-1111-7111-8111-111111111111"),
        outcome=DecisionOutcome.APPROVED,
        rule_results=(
            RuleResult(rule_id="MODE.001", outcome=RuleOutcome.PASS, message="ok", evidence={}),
            RuleResult(
                rule_id="ORDER.001",
                outcome=RuleOutcome.PASS,
                message="within lot size",
                evidence={"lot_size": "1"},
            ),
        ),
        risk_config_id=RiskConfigId("55555555-5555-7555-8555-555555555555"),
        limit_snapshot_hash="deadbeef",
        decided_at=_AWARE_TS,
    )
    assert engine  # module import above claims the minting capability harmlessly

    row = mappers.risk_decision_to_row(decision)
    round_tripped = mappers.row_to_risk_decision(row)

    assert round_tripped == decision
    assert round_tripped.rule_results[1].evidence == {"lot_size": "1"}
    assert round_tripped.outcome is DecisionOutcome.APPROVED


def test_order_round_trip_preserves_intent_id() -> None:
    order = Order(
        internal_order_id=OrderId("66666666-6666-7666-8666-666666666666"),
        mode=Mode.PAPER,
        proposal_id=ProposalId("11111111-1111-7111-8111-111111111111"),
        intent_id=IntentId("77777777-7777-7777-8777-777777777777"),
        idempotency_key="idem-1",
        status=OrderStatus.SUBMITTED,
        submitted_at=_AWARE_TS,
        acknowledged_at=_AWARE_TS,
        last_update_at=_AWARE_TS,
    )

    row = mappers.order_to_row(order)
    round_tripped = mappers.row_to_order(row)

    assert round_tripped == order
    assert round_tripped.status is OrderStatus.SUBMITTED
    assert round_tripped.intent_id == "77777777-7777-7777-8777-777777777777"
    assert row.intent_id == "77777777-7777-7777-8777-777777777777"


def test_order_intent_to_row_preserves_canonical_payload_and_hash() -> None:
    """`order_intent_to_row` is write-only (no `row_to_order_intent` -
    `ApprovedOrderIntent` requires a genuine `MintingCapability` to
    construct, which a row can never supply - see
    `atp_domain.ports.storage.OrderIntentRepository`'s docstring). This
    test proves the row captures the minted intent's fields losslessly by
    inspecting the row directly, not by round-tripping."""
    decision = RiskDecision(
        decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
        mode=Mode.PAPER,
        proposal_id=ProposalId("11111111-1111-7111-8111-111111111111"),
        outcome=DecisionOutcome.APPROVED,
        rule_results=(RuleResult(rule_id="MODE.001", outcome=RuleOutcome.PASS, message="ok"),),
        risk_config_id=RiskConfigId("55555555-5555-7555-8555-555555555555"),
        limit_snapshot_hash="deadbeef",
        decided_at=_AWARE_TS,
    )
    payload = CanonicalOrderPayload(
        instrument_id=InstrumentId("22222222-2222-7222-8222-222222222222"),
        side=Side.BUY,
        quantity=Quantity(Decimal("10.500000")),
        order_type=OrderType.LIMIT,
        limit_price=Price(Decimal("1234.567890")),
        trigger_price=None,
        product=Product.CNC,
    )
    intent = mint_intent_for_decision(
        decision,
        payload,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(_AWARE_TS),
    )

    row = mappers.order_intent_to_row(intent)

    assert row.intent_id == intent.intent_id
    assert row.mode == "PAPER"
    assert row.decision_id == decision.decision_id
    assert row.proposal_id == decision.proposal_id
    assert row.payload_hash == intent.payload_hash
    assert row.canonical_payload["instrument_id"] == "22222222-2222-7222-8222-222222222222"
    assert row.canonical_payload["side"] == "BUY"
    assert row.canonical_payload["quantity"] == "10.500000"
    assert row.canonical_payload["limit_price"] == "1234.567890"
    assert row.canonical_payload["trigger_price"] is None
    assert row.minted_at.tzinfo is not None
    assert row.expires_at.tzinfo is not None


def test_fill_round_trip_preserves_decimal_precision() -> None:
    fill = Fill(
        fill_id=FillId("88888888-8888-7888-8888-888888888888"),
        mode=Mode.PAPER,
        internal_order_id=OrderId("66666666-6666-7666-8666-666666666666"),
        quantity=Quantity(Decimal("3.000000")),
        price=Price(Decimal("999.990000")),
        fees=Money(Decimal("0.500000")),
        taxes=Money(Decimal("0.100000")),
        simulated=True,
        filled_at=_AWARE_TS,
    )

    row = mappers.fill_to_row(fill, source="PAPER_SIMULATOR")
    round_tripped = mappers.row_to_fill(row)

    assert round_tripped == fill
    assert round_tripped.price.value == Decimal("999.990000")
    assert row.source == "PAPER_SIMULATOR"


def test_position_round_trip_handles_negative_quantity() -> None:
    position = Position(
        position_id=PositionId("99999999-9999-7999-8999-999999999999"),
        instrument_id=InstrumentId("22222222-2222-7222-8222-222222222222"),
        mode=Mode.PAPER,
        quantity=Decimal("-5.000000"),
        average_price=Price(Decimal("100.000000")),
        realized_pnl=Money(Decimal("-25.000000")),
        unrealized_pnl=Money(Decimal("0")),
        updated_at=_AWARE_TS,
    )

    row = mappers.position_to_row(position)
    round_tripped = mappers.row_to_position(row)

    assert round_tripped == position
    assert round_tripped.quantity < 0


def test_risk_config_round_trip() -> None:
    config = RiskConfig(
        risk_config_id=RiskConfigId("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"),
        mode=Mode.PAPER,
        version=1,
        max_order_notional=Money(Decimal("500000.000000")),
        created_at=_AWARE_TS,
    )

    row = mappers.risk_config_to_row(
        config, active=True, created_by="bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb"
    )
    round_tripped = mappers.row_to_risk_config(row)

    assert round_tripped == config
    assert round_tripped.config_hash == config.config_hash
    assert row.config_hash == config.config_hash
    assert row.created_by == "bbbbbbbb-bbbb-7bbb-8bbb-bbbbbbbbbbbb"


def test_risk_config_to_row_defaults_created_by_to_none_for_bootstrap_rows() -> None:
    """docs/schemas/risk_config.md: the migration-seeded PAPER row has no
    creator - `created_by` is nullable precisely so this bootstrap case
    does not require a fabricated `core.users` row (docs/schemas/user.md:
    "No default/seeded user in any migration")."""
    config = RiskConfig(
        risk_config_id=RiskConfigId("aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"),
        mode=Mode.PAPER,
        version=1,
        max_order_notional=Money(Decimal("1000000.000000")),
        created_at=_AWARE_TS,
    )

    row = mappers.risk_config_to_row(config, active=True)

    assert row.created_by is None


def test_audit_event_round_trip_preserves_risk_rule_ids_order() -> None:
    event = AuditEvent(
        event_id=EventId("cccccccc-cccc-7ccc-8ccc-cccccccccccc"),
        correlation_id="dddddddd-dddd-7ddd-8ddd-dddddddddddd",
        occurred_at=_AWARE_TS,
        recorded_at=_AWARE_TS,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        action="RISK_DECISION_RECORDED",
        mode=Mode.PAPER,
        strategy_id=None,
        strategy_version=None,
        instrument_id=InstrumentId("22222222-2222-7222-8222-222222222222"),
        source_refs=MappingProxyType({"proposal_id": "11111111-1111-7111-8111-111111111111"}),
        input_hash="abc123",
        decision="APPROVED",
        risk_rule_ids=("MODE.001", "ORDER.001", "CAPITAL.001"),
    )

    row = mappers.audit_event_to_row(event)
    round_tripped = mappers.row_to_audit_event(row)

    assert round_tripped == event
    assert round_tripped.risk_rule_ids == ("MODE.001", "ORDER.001", "CAPITAL.001")


def test_money_backed_columns_never_pass_through_float() -> None:
    """A regression guard for CLAUDE.md rule #10 / rules/05-testing.md:
    Price/Quantity/Money already reject float at construction, so this just
    proves the mapper never introduces one on the way to the ORM row."""
    fill = Fill(
        fill_id=FillId("88888888-8888-7888-8888-888888888888"),
        mode=Mode.PAPER,
        internal_order_id=OrderId("66666666-6666-7666-8666-666666666666"),
        quantity=Quantity(Decimal("1")),
        price=Price(Decimal("1")),
        fees=Money(Decimal("0")),
        taxes=Money(Decimal("0")),
        simulated=True,
        filled_at=_AWARE_TS,
    )
    row = mappers.fill_to_row(fill, source="PAPER_SIMULATOR")
    for value in (row.quantity, row.price, row.fees, row.taxes):
        assert isinstance(value, Decimal)
        assert not isinstance(value, float)
