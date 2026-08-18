"""Tests for atp_domain.risk.engine.evaluate() and mint_intent_for_decision().

Covers both: (1) the aggregator's behaviour against small, isolated fake
rule sets (every rule produces a result; any INDETERMINATE/REJECT causes
overall REJECTED), and (2) the real DEFAULT_REGISTRY end to end (PAPER can
reach APPROVED with a well-formed context; LIVE never can).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from atp_domain.clock import FrozenClock
from atp_domain.errors import IntentMintingNotPermittedError
from atp_domain.ids import SequentialIdGenerator
from atp_domain.intents import CanonicalOrderPayload
from atp_domain.killswitch import SwitchId, SwitchScope, SwitchState
from atp_domain.money import Money, Price, Quantity
from atp_domain.proposals import TradeProposal
from atp_domain.risk.catalog import DEFAULT_REGISTRY
from atp_domain.risk.config import RiskConfig
from atp_domain.risk.engine import RiskDecision, evaluate, mint_intent_for_decision
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.registry import RuleRegistry
from atp_domain.risk.rule import RuleContext
from atp_domain.types import (
    DecisionId,
    DecisionOutcome,
    InstrumentId,
    Mode,
    OrderType,
    Product,
    ProposalId,
    RiskConfigId,
    Side,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _config(mode: Mode = Mode.PAPER, max_notional: str = "100000") -> RiskConfig:
    return RiskConfig(
        risk_config_id=RiskConfigId("11111111-1111-7111-8111-111111111111"),
        mode=mode,
        version=1,
        max_order_notional=Money(Decimal(max_notional)),
        created_at=NOW,
    )


def _proposal(
    *,
    mode: Mode = Mode.PAPER,
    quantity: int = 10,
    limit_price: str | None = "100",
    order_type: OrderType = OrderType.LIMIT,
) -> TradeProposal:
    return TradeProposal(
        proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
        mode=mode,
        instrument_id=InstrumentId("33333333-3333-7333-8333-333333333333"),
        side=Side.BUY,
        quantity=Quantity(Decimal(quantity)),
        order_type=order_type,
        limit_price=Price(Decimal(limit_price)) if limit_price is not None else None,
        trigger_price=None,
        product=Product.CNC,
        client_request_id="req-1",
        created_at=NOW,
    )


# ---------------------------------------------------------------------------
# Aggregator behaviour against small, isolated fake rule sets
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _FixtureRule:
    rid: str
    fixed_outcome: RuleOutcome

    @property
    def rule_id(self) -> str:
        return self.rid

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        return RuleResult(rule_id=self.rid, outcome=self.fixed_outcome, message="fixture")


def _make_rule(rule_id: str, outcome: RuleOutcome) -> _FixtureRule:
    return _FixtureRule(rid=rule_id, fixed_outcome=outcome)


def _context(config: RiskConfig, *, paper_disengaged: bool = True) -> RuleContext:
    states = {}
    if paper_disengaged:
        states[SwitchId(SwitchScope.PAPER)] = SwitchState.DISENGAGED
    return RuleContext(config=config, kill_switch_states=states)


def test_every_registered_rule_produces_exactly_one_result() -> None:
    registry = RuleRegistry()
    registry.register("A.001", Mode.PAPER, _make_rule("A.001", RuleOutcome.PASS))
    registry.register("A.002", Mode.PAPER, _make_rule("A.002", RuleOutcome.PASS))
    registry.register("A.003", Mode.PAPER, _make_rule("A.003", RuleOutcome.PASS))

    decision = evaluate(
        _proposal(),
        _context(_config()),
        registry,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )

    assert len(decision.rule_results) == 3
    assert {r.rule_id for r in decision.rule_results} == {"A.001", "A.002", "A.003"}


def test_all_pass_yields_approved() -> None:
    registry = RuleRegistry()
    registry.register("A.001", Mode.PAPER, _make_rule("A.001", RuleOutcome.PASS))
    registry.register("A.002", Mode.PAPER, _make_rule("A.002", RuleOutcome.PASS))

    decision = evaluate(
        _proposal(),
        _context(_config()),
        registry,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )

    assert decision.outcome is DecisionOutcome.APPROVED


def test_any_reject_causes_overall_reject() -> None:
    registry = RuleRegistry()
    registry.register("A.001", Mode.PAPER, _make_rule("A.001", RuleOutcome.PASS))
    registry.register("A.002", Mode.PAPER, _make_rule("A.002", RuleOutcome.REJECT))

    decision = evaluate(
        _proposal(),
        _context(_config()),
        registry,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )

    assert decision.outcome is DecisionOutcome.REJECTED


def test_any_indeterminate_causes_overall_reject() -> None:
    registry = RuleRegistry()
    registry.register("A.001", Mode.PAPER, _make_rule("A.001", RuleOutcome.PASS))
    registry.register("A.002", Mode.PAPER, _make_rule("A.002", RuleOutcome.INDETERMINATE))

    decision = evaluate(
        _proposal(),
        _context(_config()),
        registry,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )

    assert decision.outcome is DecisionOutcome.REJECTED


def test_empty_rule_set_for_a_mode_yields_reject_not_approved() -> None:
    """A mode with zero registered rules must not vacuously approve -
    `all()` over an empty sequence is True in Python, which the aggregator
    explicitly guards against."""
    registry = RuleRegistry()
    decision = evaluate(
        _proposal(),
        _context(_config()),
        registry,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert decision.outcome is DecisionOutcome.REJECTED
    assert decision.rule_results == ()


# ---------------------------------------------------------------------------
# RiskDecision itself
# ---------------------------------------------------------------------------


def test_risk_decision_is_immutable() -> None:
    registry = RuleRegistry()
    registry.register("A.001", Mode.PAPER, _make_rule("A.001", RuleOutcome.PASS))
    decision = evaluate(
        _proposal(),
        _context(_config()),
        registry,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.outcome = DecisionOutcome.APPROVED  # type: ignore[misc]


def test_risk_decision_rejects_inconsistent_outcome() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        RiskDecision(
            decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
            mode=Mode.PAPER,
            proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
            outcome=DecisionOutcome.APPROVED,
            rule_results=(RuleResult(rule_id="A.001", outcome=RuleOutcome.REJECT, message="x"),),
            risk_config_id=RiskConfigId("11111111-1111-7111-8111-111111111111"),
            limit_snapshot_hash="deadbeef",
            decided_at=NOW,
        )


def test_risk_decision_with_empty_rule_results_accepts_only_rejected() -> None:
    """Empty rule_results can never satisfy "unanimous PASS" - REJECTED is
    the only outcome the consistency check will accept for it."""
    RiskDecision(
        decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
        mode=Mode.PAPER,
        proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
        outcome=DecisionOutcome.REJECTED,
        rule_results=(),
        risk_config_id=RiskConfigId("11111111-1111-7111-8111-111111111111"),
        limit_snapshot_hash="deadbeef",
        decided_at=NOW,
    )  # does not raise

    with pytest.raises(ValueError, match="inconsistent"):
        RiskDecision(
            decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
            mode=Mode.PAPER,
            proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
            outcome=DecisionOutcome.APPROVED,
            rule_results=(),
            risk_config_id=RiskConfigId("11111111-1111-7111-8111-111111111111"),
            limit_snapshot_hash="deadbeef",
            decided_at=NOW,
        )


# ---------------------------------------------------------------------------
# End to end against the real DEFAULT_REGISTRY
# ---------------------------------------------------------------------------


def test_paper_proposal_is_approved_with_a_well_formed_context() -> None:
    context = RuleContext(
        config=_config(Mode.PAPER),
        kill_switch_states={SwitchId(SwitchScope.PAPER): SwitchState.DISENGAGED},
        available_cash=Money(Decimal("100000")),
        instrument_lot_size=1,
        instrument_tick_size=Price(Decimal("0.05")),
    )
    decision = evaluate(
        _proposal(limit_price="100"),
        context,
        DEFAULT_REGISTRY,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert decision.outcome is DecisionOutcome.APPROVED
    assert len(decision.rule_results) == 7


def test_paper_proposal_rejected_when_kill_switch_engaged() -> None:
    context = RuleContext(
        config=_config(Mode.PAPER),
        kill_switch_states={SwitchId(SwitchScope.PAPER): SwitchState.ENGAGED},
        available_cash=Money(Decimal("100000")),
        instrument_lot_size=1,
    )
    decision = evaluate(
        _proposal(),
        context,
        DEFAULT_REGISTRY,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert decision.outcome is DecisionOutcome.REJECTED


def test_paper_proposal_rejected_when_context_data_missing_fails_closed() -> None:
    """No lot size, no cash supplied - both real rules that need them
    return INDETERMINATE, which the aggregator rejects."""
    context = RuleContext(
        config=_config(Mode.PAPER),
        kill_switch_states={SwitchId(SwitchScope.PAPER): SwitchState.DISENGAGED},
    )
    decision = evaluate(
        _proposal(),
        context,
        DEFAULT_REGISTRY,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert decision.outcome is DecisionOutcome.REJECTED
    indeterminate_ids = {
        r.rule_id for r in decision.rule_results if r.outcome is RuleOutcome.INDETERMINATE
    }
    assert indeterminate_ids  # at least lot-size and cash rules are indeterminate


def test_paper_market_proposal_is_rejected_deterministically() -> None:
    """Step 9 D3: a PAPER MARKET proposal must never be approved, and no
    price is ever invented for it - RISK.DATA.001 (the real Phase 1
    implementation of the "priced reference" check) returns INDETERMINATE
    for MARKET, which the aggregator collapses to REJECTED, the same as
    RISK.CAPITAL.001 and RISK.LIMIT.001 independently do for the same
    reason."""
    context = RuleContext(
        config=_config(Mode.PAPER),
        kill_switch_states={SwitchId(SwitchScope.PAPER): SwitchState.DISENGAGED},
        available_cash=Money(Decimal("100000")),
        instrument_lot_size=1,
        instrument_tick_size=Price(Decimal("0.05")),
    )
    decision = evaluate(
        _proposal(order_type=OrderType.MARKET, limit_price=None),
        context,
        DEFAULT_REGISTRY,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert decision.outcome is DecisionOutcome.REJECTED
    data_result = next(r for r in decision.rule_results if r.rule_id == "RISK.DATA.001")
    assert data_result.outcome is RuleOutcome.INDETERMINATE


def test_live_proposal_can_never_be_approved() -> None:
    """Every one of the 28 canonical LIVE rules is a hardcoded
    INDETERMINATE stub - there is no context data that could make this
    APPROVED."""
    context = RuleContext(
        config=_config(Mode.LIVE),
        kill_switch_states={
            SwitchId(SwitchScope.GLOBAL_LIVE): SwitchState.DISENGAGED,
            SwitchId(SwitchScope.LIVE_ACCOUNT): SwitchState.DISENGAGED,
        },
        available_cash=Money(Decimal("1000000")),
        instrument_lot_size=1,
        instrument_tick_size=Price(Decimal("0.05")),
    )
    decision = evaluate(
        _proposal(mode=Mode.LIVE),
        context,
        DEFAULT_REGISTRY,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert decision.outcome is DecisionOutcome.REJECTED
    assert len(decision.rule_results) == 28
    assert all(r.outcome is RuleOutcome.INDETERMINATE for r in decision.rule_results)


# ---------------------------------------------------------------------------
# Minting is gated on APPROVED
# ---------------------------------------------------------------------------


def _payload() -> CanonicalOrderPayload:
    return CanonicalOrderPayload(
        instrument_id=InstrumentId("33333333-3333-7333-8333-333333333333"),
        side=Side.BUY,
        quantity=Quantity(Decimal(10)),
        order_type=OrderType.LIMIT,
        limit_price=Price(Decimal("100")),
        trigger_price=None,
        product=Product.CNC,
    )


def test_mint_intent_for_decision_succeeds_for_approved_decision() -> None:
    context = RuleContext(
        config=_config(Mode.PAPER),
        kill_switch_states={SwitchId(SwitchScope.PAPER): SwitchState.DISENGAGED},
        available_cash=Money(Decimal("100000")),
        instrument_lot_size=1,
        instrument_tick_size=Price(Decimal("0.05")),
    )
    decision = evaluate(
        _proposal(),
        context,
        DEFAULT_REGISTRY,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert decision.outcome is DecisionOutcome.APPROVED

    intent = mint_intent_for_decision(
        decision,
        _payload(),
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert intent.decision_id == decision.decision_id
    assert intent.mode is Mode.PAPER


def test_mint_intent_for_decision_rejects_a_rejected_decision() -> None:
    registry = RuleRegistry()
    registry.register("A.001", Mode.PAPER, _make_rule("A.001", RuleOutcome.REJECT))
    decision = evaluate(
        _proposal(),
        _context(_config()),
        registry,
        id_generator=SequentialIdGenerator(),
        clock=FrozenClock(NOW),
    )
    assert decision.outcome is DecisionOutcome.REJECTED

    with pytest.raises(IntentMintingNotPermittedError):
        mint_intent_for_decision(
            decision,
            _payload(),
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(NOW),
        )
