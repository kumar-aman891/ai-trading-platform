"""Assembles `RuleContext` from authoritative database state and calls
`atp_domain.risk.engine.evaluate()` (ADR-011).

`TradeProposal.expected_risk` is never read here - it is advisory-only
caller metadata (docs/schemas/trade_proposal.md: "the caller's own risk
estimate — advisory only, never trusted by the risk engine"), and no
function in this module accepts it or any other caller-provided substitute
for the authoritative sources listed below.
"""

from __future__ import annotations

from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.money import Money, Price
from atp_domain.proposals import TradeProposal
from atp_domain.risk.catalog import DEFAULT_REGISTRY
from atp_domain.risk.engine import RiskDecision, evaluate
from atp_domain.risk.rule import RuleContext
from atp_exec_paper.kill_switch_adapter import load_kill_switch_states
from atp_exec_paper.uow import PaperExecutionUnitOfWork


class RiskConfigUnavailableError(RuntimeError):
    """No active `RiskConfig` row exists for this proposal's mode. Fails
    closed by refusing to evaluate at all - there is no valid
    `RuleContext` without one, and CLAUDE.md rule #7 forbids silently
    substituting a guessed default."""


async def build_rule_context(uow: PaperExecutionUnitOfWork, proposal: TradeProposal) -> RuleContext:
    """Every field is populated strictly from authoritative sources:
    `core.risk_config` (active, immutable), `core.instruments`
    (lot/tick size), the PAPER kill-switch state (fail-closed adapter), and
    `paper.cash_ledger` (running balance). Nothing here reads
    `proposal.expected_risk` or accepts a caller-supplied substitute for
    any of these."""
    config = await uow.risk_config.get_active(proposal.mode)
    if config is None:
        raise RiskConfigUnavailableError(
            f"No active RiskConfig for mode={proposal.mode.value}; refusing to evaluate."
        )

    kill_switch_states = await load_kill_switch_states(uow.kill_switches)

    instrument = await uow.instruments.get(proposal.instrument_id)
    instrument_lot_size = instrument.lot_size if instrument is not None else None
    instrument_tick_size = Price(instrument.tick_size) if instrument is not None else None

    balance = await uow.cash_ledger.get_balance(proposal.mode)
    available_cash = Money(balance) if balance is not None else None

    return RuleContext(
        config=config,
        kill_switch_states=kill_switch_states,
        available_cash=available_cash,
        instrument_lot_size=instrument_lot_size,
        instrument_tick_size=instrument_tick_size,
    )


async def evaluate_proposal(
    uow: PaperExecutionUnitOfWork,
    proposal: TradeProposal,
    *,
    id_generator: IdGenerator,
    clock: Clock,
) -> RiskDecision:
    """Loads context strictly from authoritative sources and delegates to
    the deterministic risk engine (`atp_domain.risk.engine.evaluate`,
    reject-by-default)."""
    context = await build_rule_context(uow, proposal)
    return evaluate(proposal, context, DEFAULT_REGISTRY, id_generator=id_generator, clock=clock)
