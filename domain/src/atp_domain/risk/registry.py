"""Stable rule-ID registry.

A generic, injectable registry class - `risk.catalog` builds the one
process-wide `DEFAULT_REGISTRY` instance, but the class itself takes no
dependency on the catalog, so tests can build small registries of fake
rules in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

from atp_domain.errors import DuplicateRuleRegistrationError
from atp_domain.risk.rule import Rule
from atp_domain.types import Mode


@dataclass(frozen=True, slots=True)
class RuleRegistration:
    rule_id: str
    mode: Mode
    rule: Rule


class RuleRegistry:
    def __init__(self) -> None:
        self._registrations: dict[tuple[str, Mode], RuleRegistration] = {}

    def register(self, rule_id: str, mode: Mode, rule: Rule) -> None:
        key = (rule_id, mode)
        if key in self._registrations:
            raise DuplicateRuleRegistrationError(
                f"Rule {rule_id!r} is already registered for mode {mode}."
            )
        if rule.rule_id != rule_id:
            raise DuplicateRuleRegistrationError(
                f"Rule object's rule_id {rule.rule_id!r} does not match the "
                f"registration key {rule_id!r}."
            )
        self._registrations[key] = RuleRegistration(rule_id=rule_id, mode=mode, rule=rule)

    def rules_for_mode(self, mode: Mode) -> tuple[RuleRegistration, ...]:
        return tuple(
            registration
            for registration in self._registrations.values()
            if registration.mode is mode
        )

    def rule_ids_for_mode(self, mode: Mode) -> frozenset[str]:
        return frozenset(registration.rule_id for registration in self.rules_for_mode(mode))

    def all_rule_ids(self) -> frozenset[str]:
        return frozenset(rule_id for rule_id, _mode in self._registrations)

    def __len__(self) -> int:
        return len(self._registrations)
