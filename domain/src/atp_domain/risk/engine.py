"""The deterministic risk engine: evaluate() -> RiskDecision, reject by
default. The only module permitted to mint an ApprovedOrderIntent
(ADR-008) - every call to atp_domain.intents.mint_approved_order_intent
must originate here.

"Only this module may mint" is enforced by capability, not by call-stack
introspection: at import time, this module calls
`atp_domain.intents.issue_minting_capability()` exactly once and holds the
single resulting `MintingCapability` in `_CAPABILITY` below. That call can
succeed at most once per process, so no other module can ever hold a
genuine capability - see atp_domain.intents' module docstring for the full
mechanism.

Reject-by-default is structural, not a branch that could be inverted: the
aggregation in `evaluate()` computes APPROVED only when every rule result
is PASS. A single REJECT or a single INDETERMINATE both fail that
condition identically, so "any INDETERMINATE causes REJECT" isn't a special
case being handled - it falls out of `all(r.outcome is PASS ...)` being
False for anything other than unanimous PASS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atp_domain.clock import Clock
from atp_domain.errors import IntentMintingNotPermittedError
from atp_domain.ids import IdGenerator
from atp_domain.intents import (
    DEFAULT_INTENT_TTL_SECONDS,
    ApprovedOrderIntent,
    CanonicalOrderPayload,
    issue_minting_capability,
    mint_approved_order_intent,
)
from atp_domain.proposals import TradeProposal
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.registry import RuleRegistry
from atp_domain.risk.rule import RuleContext
from atp_domain.types import DecisionId, DecisionOutcome, Mode, ProposalId, RiskConfigId

# Claimed exactly once, at import time. This module is the only holder of
# a genuine MintingCapability for the lifetime of the process.
_CAPABILITY = issue_minting_capability()


@dataclass(frozen=True, slots=True)
class RiskDecision:
    decision_id: DecisionId
    mode: Mode
    proposal_id: ProposalId
    outcome: DecisionOutcome
    rule_results: tuple[RuleResult, ...]
    risk_config_id: RiskConfigId
    limit_snapshot_hash: str
    decided_at: datetime

    def __post_init__(self) -> None:
        if self.decided_at.tzinfo is None:
            raise ValueError("decided_at must be timezone-aware.")

        # Empty rule_results (a mode with no registered rules at all) is
        # not a special case handled separately - it simply can never
        # satisfy "unanimous PASS", so REJECTED is the only valid outcome,
        # exactly like a real REJECT or INDETERMINATE result would force.
        all_pass = bool(self.rule_results) and all(
            result.outcome is RuleOutcome.PASS for result in self.rule_results
        )
        expected_outcome = DecisionOutcome.APPROVED if all_pass else DecisionOutcome.REJECTED
        if self.outcome is not expected_outcome:
            raise ValueError(
                f"outcome={self.outcome} is inconsistent with rule_results "
                f"(expected {expected_outcome} given the recorded results)."
            )


def evaluate(
    proposal: TradeProposal,
    context: RuleContext,
    registry: RuleRegistry,
    *,
    id_generator: IdGenerator,
    clock: Clock,
) -> RiskDecision:
    """Evaluate every rule registered for `proposal.mode` and produce a
    RiskDecision. Every registered rule for the mode produces exactly one
    RuleResult - none are skipped, none are optional."""
    registrations = registry.rules_for_mode(proposal.mode)
    rule_results = tuple(
        registration.rule.check(proposal, context) for registration in registrations
    )

    all_pass = bool(rule_results) and all(
        result.outcome is RuleOutcome.PASS for result in rule_results
    )
    outcome = DecisionOutcome.APPROVED if all_pass else DecisionOutcome.REJECTED

    return RiskDecision(
        decision_id=DecisionId(id_generator.new_id()),
        mode=proposal.mode,
        proposal_id=proposal.proposal_id,
        outcome=outcome,
        rule_results=rule_results,
        risk_config_id=context.config.risk_config_id,
        limit_snapshot_hash=context.config.config_hash,
        decided_at=clock.now(),
    )


def mint_intent_for_decision(
    decision: RiskDecision,
    canonical_payload: CanonicalOrderPayload,
    *,
    id_generator: IdGenerator,
    clock: Clock,
    ttl_seconds: int = DEFAULT_INTENT_TTL_SECONDS,
) -> ApprovedOrderIntent:
    """Mint an ApprovedOrderIntent from an APPROVED RiskDecision. This is
    the only call site of atp_domain.intents.mint_approved_order_intent in
    the entire codebase, and the only place `_CAPABILITY` is used -
    enforced by tests (tests/unit/domain/test_intents.py)."""
    if decision.outcome is not DecisionOutcome.APPROVED:
        raise IntentMintingNotPermittedError(
            f"Cannot mint an intent from a {decision.outcome.value} decision "
            f"({decision.decision_id})."
        )
    return mint_approved_order_intent(
        capability=_CAPABILITY,
        id_generator=id_generator,
        clock=clock,
        decision_id=decision.decision_id,
        mode=decision.mode,
        proposal_id=decision.proposal_id,
        canonical_payload=canonical_payload,
        ttl_seconds=ttl_seconds,
    )
