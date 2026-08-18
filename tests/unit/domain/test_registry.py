"""Tests for atp_domain.risk.registry.RuleRegistry, exercised both in
isolation (fake rules) and against the real DEFAULT_REGISTRY - the latter
is what makes a missing/removed canonical rule ID detectable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pytest

from atp_domain.errors import DuplicateRuleRegistrationError
from atp_domain.proposals import TradeProposal
from atp_domain.risk.catalog import CANONICAL_RULE_IDS, DEFAULT_REGISTRY, PAPER_APPROVED_RULE_IDS
from atp_domain.risk.outcomes import RuleOutcome, RuleResult
from atp_domain.risk.registry import RuleRegistry
from atp_domain.risk.rule import RuleContext
from atp_domain.types import Mode


@dataclass(frozen=True, slots=True)
class _FakeRule:
    rule_id: ClassVar[str] = "FAKE.001"

    def check(self, proposal: TradeProposal, context: RuleContext) -> RuleResult:
        return RuleResult(rule_id=self.rule_id, outcome=RuleOutcome.PASS, message="ok")


def test_register_and_retrieve_by_mode() -> None:
    registry = RuleRegistry()
    registry.register("FAKE.001", Mode.PAPER, _FakeRule())

    registrations = registry.rules_for_mode(Mode.PAPER)
    assert len(registrations) == 1
    assert registrations[0].rule_id == "FAKE.001"
    assert registry.rules_for_mode(Mode.LIVE) == ()


def test_duplicate_registration_for_same_mode_is_rejected() -> None:
    registry = RuleRegistry()
    registry.register("FAKE.001", Mode.PAPER, _FakeRule())

    with pytest.raises(DuplicateRuleRegistrationError):
        registry.register("FAKE.001", Mode.PAPER, _FakeRule())


def test_same_rule_id_may_be_registered_once_per_mode() -> None:
    registry = RuleRegistry()
    registry.register("FAKE.001", Mode.PAPER, _FakeRule())
    registry.register("FAKE.001", Mode.LIVE, _FakeRule())  # does not raise

    assert len(registry) == 2


def test_register_rejects_mismatched_rule_id() -> None:
    registry = RuleRegistry()
    with pytest.raises(DuplicateRuleRegistrationError, match="does not match"):
        registry.register("WRONG.ID", Mode.PAPER, _FakeRule())


def test_default_registry_contains_every_canonical_id_under_live() -> None:
    """The registry makes a missing/removed rule detectable: if a
    canonical ID were deleted from the catalogue, this assertion fails."""
    assert len(CANONICAL_RULE_IDS) == 28
    assert DEFAULT_REGISTRY.rule_ids_for_mode(Mode.LIVE) == CANONICAL_RULE_IDS


def test_default_registry_paper_set_is_exactly_the_seven_approved_rules() -> None:
    assert len(PAPER_APPROVED_RULE_IDS) == 7
    assert DEFAULT_REGISTRY.rule_ids_for_mode(Mode.PAPER) == PAPER_APPROVED_RULE_IDS
    assert PAPER_APPROVED_RULE_IDS.issubset(CANONICAL_RULE_IDS)


def test_default_registry_live_set_never_shrinks_below_canonical() -> None:
    """A stronger phrasing of the same invariant: every canonical ID must
    resolve to a real LIVE registration, individually."""
    live_ids = DEFAULT_REGISTRY.rule_ids_for_mode(Mode.LIVE)
    missing = CANONICAL_RULE_IDS - live_ids
    assert missing == set(), f"Canonical rule IDs missing from LIVE registration: {missing}"
