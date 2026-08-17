"""Builds DEFAULT_REGISTRY: the complete canonical ~28 rule IDs, each
registered under LIVE as an always-INDETERMINATE stub, plus the six IDs
that additionally have a real PAPER implementation
(docs - approved Phase 1 plan §11.2-11.3).

PAPER's rule set is *exactly* the six real rules - the other 22 canonical
IDs are never registered under PAPER at all, so "any INDETERMINATE causes
REJECT" applies with no carve-out: PAPER can reach APPROVED because its
rule set contains nothing that returns a hardcoded INDETERMINATE, while
LIVE's rule set is the full 28, every one of them a stub, guaranteeing LIVE
can never reach APPROVED (risk.engine's evaluate()).

CANONICAL_RULE_IDS and PAPER_APPROVED_RULE_IDS exist so a test can assert
the registry's actual contents equal the expected sets exactly - a removed
or renamed rule ID fails that test immediately (requirement: "the registry
must make missing/removed rules detectable by tests").
"""

from __future__ import annotations

from atp_domain.risk.catalog import (
    capital_rules,
    data_rules,
    instrument_rules,
    limit_rules,
    mode_rules,
    order_rules,
    session_rules,
)
from atp_domain.risk.catalog.stubs import NotImplementedRule
from atp_domain.risk.registry import RuleRegistry
from atp_domain.risk.rule import Rule
from atp_domain.types import Mode

# (rule_id, description) for every canonical Phase 1 rule - the union of
# rules/02-live-trading.md and docs/RISK_AND_GUARDRAILS.md's checklists,
# de-duplicated and grouped into families.
_MODE_RULES: tuple[tuple[str, str], ...] = (
    (
        mode_rules.RULE_ID_MODE_MATCHES_CONFIG,
        "Explicit mode guard - proposal mode matches risk config mode",
    ),
    (mode_rules.RULE_ID_KILL_SWITCH, "Kill-switch status check"),
    (
        mode_rules.RULE_ID_LIVE_ACTIVATION,
        "Live trading enabled by user (explicit runtime activation)",
    ),
)
_ORDER_RULES: tuple[tuple[str, str], ...] = (
    (order_rules.RULE_ID_LOT_TICK, "Order quantity/price respects lot/tick rules"),
    (order_rules.RULE_ID_TYPE_PRICE_COHERENCE, "Order type / price-field coherence"),
    (order_rules.RULE_ID_DUPLICATE_ORDER, "Duplicate-order / idempotency check"),
    (order_rules.RULE_ID_ORDER_RATE, "Order-rate / OPS limit"),
    (order_rules.RULE_ID_MAX_TURNOVER, "Maximum turnover/day"),
)
_CAPITAL_RULES: tuple[tuple[str, str], ...] = (
    (capital_rules.RULE_ID_SIMULATED_CASH, "Sufficient available funds/cash"),
    (capital_rules.RULE_ID_MARGIN, "Sufficient available margin"),
)
_LIMIT_RULES: tuple[tuple[str, str], ...] = (
    (limit_rules.RULE_ID_MAX_ORDER_NOTIONAL, "Maximum order notional"),
    (limit_rules.RULE_ID_PER_TRADE_MAX_LOSS, "Per-trade maximum loss"),
    (limit_rules.RULE_ID_DAILY_LOSS_LIMIT, "Daily realized + unrealized loss limit"),
    (limit_rules.RULE_ID_CONCENTRATION, "Symbol/position concentration limit"),
    (limit_rules.RULE_ID_SECTOR_CONCENTRATION, "Sector/portfolio concentration limit"),
    (limit_rules.RULE_ID_STRATEGY_ALLOCATION, "Strategy max allocation / exposure limit"),
    (limit_rules.RULE_ID_POSITION_LIMITS, "Position limits"),
)

CANONICAL_RULES: tuple[tuple[str, str], ...] = (
    _MODE_RULES
    + session_rules.CANONICAL_RULES
    + instrument_rules.CANONICAL_RULES
    + _ORDER_RULES
    + _CAPITAL_RULES
    + _LIMIT_RULES
    + data_rules.CANONICAL_RULES
)

CANONICAL_RULE_IDS: frozenset[str] = frozenset(rule_id for rule_id, _description in CANONICAL_RULES)

PAPER_APPROVED_RULE_IDS: frozenset[str] = frozenset(
    {
        mode_rules.RULE_ID_MODE_MATCHES_CONFIG,
        mode_rules.RULE_ID_KILL_SWITCH,
        order_rules.RULE_ID_LOT_TICK,
        order_rules.RULE_ID_TYPE_PRICE_COHERENCE,
        limit_rules.RULE_ID_MAX_ORDER_NOTIONAL,
        capital_rules.RULE_ID_SIMULATED_CASH,
    }
)

_PAPER_REAL_RULES: dict[str, Rule] = {
    mode_rules.RULE_ID_MODE_MATCHES_CONFIG: mode_rules.ModeMatchesConfigRule(),
    mode_rules.RULE_ID_KILL_SWITCH: mode_rules.PaperKillSwitchRule(),
    order_rules.RULE_ID_LOT_TICK: order_rules.LotTickValidationRule(),
    order_rules.RULE_ID_TYPE_PRICE_COHERENCE: order_rules.OrderTypePriceCoherenceRule(),
    limit_rules.RULE_ID_MAX_ORDER_NOTIONAL: limit_rules.MaxOrderNotionalRule(),
    capital_rules.RULE_ID_SIMULATED_CASH: capital_rules.SimulatedCashSufficiencyRule(),
}


def build_default_registry() -> RuleRegistry:
    registry = RuleRegistry()

    for rule_id, description in CANONICAL_RULES:
        registry.register(rule_id, Mode.LIVE, NotImplementedRule(rule_id, description))

    for rule_id in PAPER_APPROVED_RULE_IDS:
        registry.register(rule_id, Mode.PAPER, _PAPER_REAL_RULES[rule_id])

    return registry


DEFAULT_REGISTRY: RuleRegistry = build_default_registry()

__all__ = [
    "CANONICAL_RULES",
    "CANONICAL_RULE_IDS",
    "DEFAULT_REGISTRY",
    "PAPER_APPROVED_RULE_IDS",
    "build_default_registry",
]
