"""Tests for atp_domain.intents - the CRITICAL invariant: only
atp_domain.risk.engine may mint an ApprovedOrderIntent (ADR-008), enforced
via the MintingCapability issuance mechanism (not call-stack introspection).

Importing atp_domain.risk.engine here (even though nothing else in this
file calls it directly) guarantees the single capability has already been
claimed before any test in this module runs - which is exactly the
scenario these tests are proving is safe: an arbitrary caller, running
after the legitimate holder already exists, still cannot get one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import atp_domain.risk.engine  # noqa: F401 - import for its side effect: claims the capability
from atp_domain.clock import FrozenClock
from atp_domain.errors import ExpiredIntentError, IntentMintingNotPermittedError
from atp_domain.ids import SequentialIdGenerator
from atp_domain.intents import (
    ApprovedOrderIntent,
    CanonicalOrderPayload,
    MintingCapability,
    issue_minting_capability,
    mint_approved_order_intent,
)
from atp_domain.money import Price, Quantity
from atp_domain.risk.engine import RiskDecision, mint_intent_for_decision
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.types import (
    DecisionId,
    DecisionOutcome,
    InstrumentId,
    IntentId,
    Mode,
    OrderType,
    Product,
    ProposalId,
    RiskConfigId,
    Side,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


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


def test_canonical_order_payload_rejects_limit_without_price() -> None:
    with pytest.raises(ValueError, match="LIMIT orders require"):
        CanonicalOrderPayload(
            instrument_id=InstrumentId("33333333-3333-7333-8333-333333333333"),
            side=Side.BUY,
            quantity=Quantity(Decimal(10)),
            order_type=OrderType.LIMIT,
            limit_price=None,
            trigger_price=None,
            product=Product.CNC,
        )


# ---------------------------------------------------------------------------
# The capability itself cannot be forged
# ---------------------------------------------------------------------------


def test_minting_capability_cannot_be_constructed_directly() -> None:
    with pytest.raises(IntentMintingNotPermittedError, match="cannot be constructed directly"):
        MintingCapability()


def test_capability_has_already_been_claimed_by_risk_engine() -> None:
    """atp_domain.risk.engine claimed the one capability at import time
    (guaranteed by this module's own import above). A second issuance,
    from any caller, fails."""
    with pytest.raises(IntentMintingNotPermittedError, match="already been issued"):
        issue_minting_capability()


# ---------------------------------------------------------------------------
# An arbitrary caller cannot mint, by any route
# ---------------------------------------------------------------------------


def test_mint_function_rejects_a_caller_with_no_capability() -> None:
    with pytest.raises(IntentMintingNotPermittedError, match="genuine MintingCapability"):
        mint_approved_order_intent(
            capability=None,  # type: ignore[arg-type]
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(NOW),
            decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
            mode=Mode.PAPER,
            proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
            canonical_payload=_payload(),
        )


def test_mint_function_rejects_a_forged_capability_lookalike() -> None:
    """An object that merely resembles the capability (same shape, wrong
    type) is not accepted - the check is isinstance, not duck typing."""

    class _Forgery:
        pass

    with pytest.raises(IntentMintingNotPermittedError, match="genuine MintingCapability"):
        mint_approved_order_intent(
            capability=_Forgery(),  # type: ignore[arg-type]
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(NOW),
            decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
            mode=Mode.PAPER,
            proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
            canonical_payload=_payload(),
        )


def test_direct_dataclass_construction_without_a_capability_fails() -> None:
    """The dataclass constructor itself is not usable by arbitrary
    callers - `capability` is a required InitVar, so omitting it is a
    TypeError before __post_init__ even runs."""
    with pytest.raises(TypeError):
        ApprovedOrderIntent(  # type: ignore[call-arg]
            intent_id=IntentId("55555555-5555-7555-8555-555555555555"),
            mode=Mode.PAPER,
            decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
            proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
            canonical_payload=_payload(),
            payload_hash="deadbeef",
            minted_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
        )


def test_direct_dataclass_construction_with_a_forged_capability_fails() -> None:
    class _Forgery:
        pass

    with pytest.raises(IntentMintingNotPermittedError, match="genuine MintingCapability"):
        ApprovedOrderIntent(
            intent_id=IntentId("55555555-5555-7555-8555-555555555555"),
            mode=Mode.PAPER,
            decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
            proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
            canonical_payload=_payload(),
            payload_hash="deadbeef",
            minted_at=NOW,
            expires_at=NOW + timedelta(seconds=30),
            capability=_Forgery(),  # type: ignore[arg-type]
        )


def test_capability_is_not_importable_as_a_ready_made_instance() -> None:
    """There is no module-level pre-built capability sitting in
    atp_domain.intents for an arbitrary caller to import and reuse - the
    only way to end up holding one is via issue_minting_capability(),
    which is already exhausted by the time this test runs."""
    import atp_domain.intents as intents_module

    for name in dir(intents_module):
        if name.startswith("_"):
            continue
        value = getattr(intents_module, name)
        assert not isinstance(
            value, MintingCapability
        ), f"Found a ready-made MintingCapability exposed as atp_domain.intents.{name}"


# ---------------------------------------------------------------------------
# The sanctioned path (via atp_domain.risk.engine) still works
# ---------------------------------------------------------------------------


def test_expired_intent_via_the_sanctioned_path() -> None:
    """Mints through the real, sanctioned path (risk.engine) and checks
    expiry - the one legitimate way to obtain an instance to test against."""
    decision = RiskDecision(
        decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
        mode=Mode.PAPER,
        proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
        outcome=DecisionOutcome.APPROVED,
        rule_results=(RuleResult(rule_id="A.001", outcome=RuleOutcome.PASS, message="ok"),),
        risk_config_id=RiskConfigId("11111111-1111-7111-8111-111111111111"),
        limit_snapshot_hash="deadbeef",
        decided_at=NOW,
    )
    clock = FrozenClock(NOW)
    intent = mint_intent_for_decision(
        decision, _payload(), id_generator=SequentialIdGenerator(), clock=clock, ttl_seconds=30
    )

    assert intent.is_expired(at=NOW) is False
    assert intent.is_expired(at=NOW + timedelta(seconds=30)) is True
    assert intent.is_expired(at=NOW + timedelta(seconds=29, milliseconds=999)) is False

    with pytest.raises(ExpiredIntentError):
        intent.require_not_expired(at=NOW + timedelta(seconds=31))

    intent.require_not_expired(at=NOW + timedelta(seconds=1))  # does not raise


def test_mint_function_rejects_non_positive_ttl_even_with_a_forged_capability_absent() -> None:
    """ttl validation is unreachable without first passing the capability
    check - proof the capability gate runs first, not a bypassable
    afterthought."""
    with pytest.raises(IntentMintingNotPermittedError):
        mint_approved_order_intent(
            capability=None,  # type: ignore[arg-type]
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(NOW),
            decision_id=DecisionId("44444444-4444-7444-8444-444444444444"),
            mode=Mode.PAPER,
            proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
            canonical_payload=_payload(),
            ttl_seconds=-1,
        )
