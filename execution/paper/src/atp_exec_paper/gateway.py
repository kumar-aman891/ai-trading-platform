"""The paper execution gateway (ADR-008, ADR-011).

The only external input to `execute_proposal`/`run_once` is a
`proposal_id`. Neither function, nor anything else in this module, accepts
a symbol, instrument, quantity, price, order type, side, product, or any
other order field directly - `tests/safety/test_gateway_surface.py` asserts
this mechanically against the actual function signatures.

Authoritative sequence for one proposal:

    TradeProposal (reloaded from the database by proposal_id)
        -> RuleContext (atp_exec_paper.risk_runner, authoritative sources only)
        -> RiskDecision (atp_domain.risk.engine.evaluate, reject-by-default)
        -> ApprovedOrderIntent (minted only on APPROVED, atp_domain.risk.engine)
        -> Order -> Fill -> Position -> Cash ledger -> Audit event

all committed in one `PaperExecutionUnitOfWork` transaction. A REJECTED
decision commits only the `RiskDecision` and its audit event - no
order_intent/order/fill/position/cash-ledger row is ever written for it.

Two entry points:

- `run_once(session_factory, proposal_id, ...)` - one-shot; used by both
  the poll loop below and any direct/manual invocation (an operator script,
  a test). Opens and manages its own transaction.
- `run_poll_loop(session_factory, ...)` - the DB-polled claim loop
  (ADR-011): repeatedly lists unevaluated PAPER proposals (a plain SELECT,
  no row lock - `atp_paper_exec` holds only SELECT on
  `paper.trade_proposals`, per migration 0003) and calls `run_once` for
  each. Concurrent claimants are safe because
  `paper.risk_decisions.proposal_id` is UNIQUE: a losing claimant's
  `risk_decisions` insert raises `IntegrityError`, which `run_once` catches
  (after the transaction has already been rolled back by
  `paper_execution_unit_of_work`) and reports as `already_claimed=True`,
  never as an error.

`execute_proposal` is the lower-level, directly-testable core: it takes an
already-open `PaperExecutionUnitOfWork` (or a duck-typed fake of one) and
does not manage a transaction itself - this is what lets unit tests exercise
the full gateway logic against in-memory fakes without a real database,
mirroring `atp_api.bootstrap.bootstrap_admin`'s split between transaction-
managing and pure-logic layers.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atp_domain.audit import (
    ACTION_INTENT_MINTED,
    ACTION_ORDER_SUBMITTED,
    ACTION_RISK_DECISION_RECORDED,
    AuditEvent,
)
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.intents import CanonicalOrderPayload
from atp_domain.money import Money
from atp_domain.orders import Fill, Order, Position
from atp_domain.proposals import TradeProposal
from atp_domain.risk.engine import RiskDecision, mint_intent_for_decision
from atp_domain.types import (
    ActorType,
    DecisionOutcome,
    EventId,
    FillId,
    Mode,
    OrderId,
    OrderStatus,
    PositionId,
    ProposalId,
    Side,
)
from atp_exec_paper.risk_runner import RiskConfigUnavailableError, evaluate_proposal
from atp_exec_paper.simulator import SOURCE_PAPER_SIMULATOR, simulate_fill
from atp_exec_paper.uow import PaperExecutionUnitOfWork, paper_execution_unit_of_work
from atp_platform.correlation import new_correlation_id
from atp_platform.logging import get_logger

_logger = get_logger("atp_exec_paper.gateway")

DEFAULT_POLL_INTERVAL_SECONDS = 2.0
DEFAULT_CLAIM_BATCH_SIZE = 10


class ProposalNotFoundError(RuntimeError):
    """No `paper.trade_proposals` row exists for the given `proposal_id`."""


class CashLedgerUnavailableError(RuntimeError):
    """No `paper.cash_ledger` balance exists for this mode - should not
    occur once migration 0004 has run; fails closed by refusing to record
    an execution rather than assuming a zero/unlimited balance."""


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    proposal_id: str
    decision_outcome: DecisionOutcome | None
    order_id: str | None
    already_claimed: bool


def idempotency_key(proposal: TradeProposal, mode: Mode) -> str:
    """Exactly `docs/schemas/order.md`'s specified derivation:
    `sha256(mode || client_request_id || instrument_id || side || quantity
    || order_type || limit_price || product)` - plain string concatenation
    (`||` is SQL's concatenation operator, not a delimiter to literally
    include), no alternative derivation."""
    concatenated = (
        mode.value
        + proposal.client_request_id
        + str(proposal.instrument_id)
        + proposal.side.value
        + str(proposal.quantity.value)
        + proposal.order_type.value
        + (str(proposal.limit_price.value) if proposal.limit_price is not None else "")
        + proposal.product.value
    )
    return hashlib.sha256(concatenated.encode("utf-8")).hexdigest()


async def execute_proposal(
    uow: PaperExecutionUnitOfWork,
    proposal_id: str,
    *,
    id_generator: IdGenerator,
    clock: Clock,
    correlation_id: str | None = None,
) -> ExecutionOutcome:
    """Core gateway logic against an already-open `uow`. Does not manage a
    transaction or catch `IntegrityError`/`RiskConfigUnavailableError` -
    both propagate to the caller, which is expected to be running inside
    `paper_execution_unit_of_work` (see `run_once` below) so the
    transaction is rolled back correctly before either is handled."""
    correlation_id = correlation_id or new_correlation_id()

    proposal = await uow.trade_proposals.get(ProposalId(proposal_id))
    if proposal is None:
        raise ProposalNotFoundError(f"No trade proposal found for proposal_id={proposal_id!r}.")

    now = clock.now()

    decision = await evaluate_proposal(uow, proposal, id_generator=id_generator, clock=clock)
    await uow.risk_decisions.save(decision)

    await uow.audit.save(
        AuditEvent(
            event_id=EventId(id_generator.new_id()),
            correlation_id=correlation_id,
            occurred_at=now,
            recorded_at=now,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            action=ACTION_RISK_DECISION_RECORDED,
            mode=proposal.mode,
            strategy_id=proposal.strategy_id,
            strategy_version=proposal.strategy_version,
            instrument_id=proposal.instrument_id,
            input_hash=decision.limit_snapshot_hash,
            decision=decision.outcome.value,
            risk_rule_ids=tuple(r.rule_id for r in decision.rule_results),
        )
    )

    if decision.outcome is not DecisionOutcome.APPROVED:
        return ExecutionOutcome(
            proposal_id=proposal_id,
            decision_outcome=decision.outcome,
            order_id=None,
            already_claimed=False,
        )

    order_id = await _execute_approved(
        uow,
        proposal,
        decision,
        id_generator=id_generator,
        correlation_id=correlation_id,
        now=now,
    )
    return ExecutionOutcome(
        proposal_id=proposal_id,
        decision_outcome=decision.outcome,
        order_id=order_id,
        already_claimed=False,
    )


async def _execute_approved(
    uow: PaperExecutionUnitOfWork,
    proposal: TradeProposal,
    decision: RiskDecision,
    *,
    id_generator: IdGenerator,
    correlation_id: str,
    now: datetime,
) -> str:
    """Steps 1-7 of the execution transaction (ADR-008, ADR-010): mint
    intent, insert order_intents/orders/fills, upsert positions, append
    cash_ledger, append audit events - all within the caller's already-open
    `uow`/transaction. `mint_intent_for_decision` uses `clock` internally
    only to set `minted_at`; `now` is passed through explicitly here so
    every row this execution touches shares one timestamp."""
    payload = CanonicalOrderPayload(
        instrument_id=proposal.instrument_id,
        side=proposal.side,
        quantity=proposal.quantity,
        order_type=proposal.order_type,
        limit_price=proposal.limit_price,
        trigger_price=proposal.trigger_price,
        product=proposal.product,
    )
    intent = mint_intent_for_decision(
        decision, payload, id_generator=id_generator, clock=_FixedClock(now)
    )
    await uow.order_intents.save(intent)

    order_id = OrderId(id_generator.new_id())
    submitted_order = Order(
        internal_order_id=order_id,
        mode=proposal.mode,
        proposal_id=proposal.proposal_id,
        intent_id=intent.intent_id,
        idempotency_key=idempotency_key(proposal, proposal.mode),
        status=OrderStatus.SUBMITTED,
        submitted_at=now,
        acknowledged_at=now,
        last_update_at=now,
    )

    fill_id = FillId(id_generator.new_id())
    fill = simulate_fill(proposal, fill_id=fill_id, internal_order_id=order_id, filled_at=now)

    # No latency is modelled (docs/schemas/fill.md) - the SUBMITTED state
    # is never separately persisted; only the final FILLED order is
    # written, after its transition has been validated in-memory by
    # Order.with_status (the same state-machine guard a real, slower
    # execution path would also go through).
    filled_order = submitted_order.with_status(OrderStatus.FILLED, at=now)
    await uow.orders.save(filled_order)
    await uow.fills.save(fill, source=SOURCE_PAPER_SIMULATOR)

    await _apply_position_and_cash(uow, proposal, fill, id_generator=id_generator, now=now)

    await uow.audit.save(
        AuditEvent(
            event_id=EventId(id_generator.new_id()),
            correlation_id=correlation_id,
            occurred_at=now,
            recorded_at=now,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            action=ACTION_INTENT_MINTED,
            mode=proposal.mode,
            strategy_id=proposal.strategy_id,
            strategy_version=proposal.strategy_version,
            instrument_id=proposal.instrument_id,
            input_hash=intent.payload_hash,
            decision=decision.outcome.value,
        )
    )
    await uow.audit.save(
        AuditEvent(
            event_id=EventId(id_generator.new_id()),
            correlation_id=correlation_id,
            occurred_at=now,
            recorded_at=now,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            action=ACTION_ORDER_SUBMITTED,
            mode=proposal.mode,
            strategy_id=proposal.strategy_id,
            strategy_version=proposal.strategy_version,
            instrument_id=proposal.instrument_id,
            decision=decision.outcome.value,
            risk_rule_ids=tuple(r.rule_id for r in decision.rule_results),
        )
    )

    return order_id


@dataclass(frozen=True, slots=True)
class _FixedClock:
    """A minimal `Clock` returning a fixed instant - used only to make
    `mint_intent_for_decision`'s `minted_at` share the exact same
    `now` every other row in this execution is written with, without
    threading a second, separately-ticking clock through the call."""

    _now: datetime

    def now(self) -> datetime:
        return self._now


async def _apply_position_and_cash(
    uow: PaperExecutionUnitOfWork,
    proposal: TradeProposal,
    fill: Fill,
    *,
    id_generator: IdGenerator,
    now: datetime,
) -> None:
    existing_position = await uow.positions.get(proposal.mode, proposal.instrument_id)
    if existing_position is None:
        existing_position = Position(
            position_id=PositionId(id_generator.new_id()),
            instrument_id=proposal.instrument_id,
            mode=proposal.mode,
            quantity=Decimal(0),
            average_price=None,
            realized_pnl=Money.zero(),
            unrealized_pnl=Money.zero(),
            updated_at=now,
        )
    updated_position = existing_position.apply_fill(fill, side=proposal.side, at=now)
    await uow.positions.upsert(updated_position)

    balance_before = await uow.cash_ledger.get_balance(proposal.mode)
    if balance_before is None:
        raise CashLedgerUnavailableError(
            f"No paper.cash_ledger balance exists for mode={proposal.mode.value}; refusing to "
            "record a cash effect (migration 0004 seeds the opening deposit)."
        )
    notional = fill.quantity.value * fill.price.value
    if proposal.side is Side.BUY:
        entry_type = "FILL_DEBIT"
        balance_after = balance_before - notional
    else:
        entry_type = "FILL_CREDIT"
        balance_after = balance_before + notional

    await uow.cash_ledger.append(
        entry_id=id_generator.new_id(),
        mode=proposal.mode,
        entry_type=entry_type,
        amount=notional,
        related_fill_id=fill.fill_id,
        balance_after=balance_after,
        created_at=now,
    )


async def run_once(
    session_factory: async_sessionmaker[AsyncSession],
    proposal_id: str,
    *,
    id_generator: IdGenerator,
    clock: Clock,
    correlation_id: str | None = None,
) -> ExecutionOutcome:
    """One-shot entry point: opens its own transaction
    (`paper_execution_unit_of_work`), runs `execute_proposal`, and
    translates a lost claim race (`IntegrityError` on the
    `paper.risk_decisions` insert, after the transaction has already been
    rolled back) into `already_claimed=True` rather than letting it
    surface as an unhandled error."""
    try:
        async with paper_execution_unit_of_work(session_factory) as uow:
            outcome = await execute_proposal(
                uow,
                proposal_id,
                id_generator=id_generator,
                clock=clock,
                correlation_id=correlation_id,
            )
        return outcome
    except IntegrityError:
        _logger.info(
            "proposal_already_claimed",
            proposal_id=proposal_id,
            reason="UNIQUE(proposal_id) on paper.risk_decisions - ADR-011",
        )
        return ExecutionOutcome(
            proposal_id=proposal_id,
            decision_outcome=None,
            order_id=None,
            already_claimed=True,
        )


async def _list_candidate_proposal_ids(
    session_factory: async_sessionmaker[AsyncSession], *, batch_size: int
) -> Sequence[str]:
    async with session_factory() as session:
        uow = PaperExecutionUnitOfWork(session)
        candidate_ids = await uow.trade_proposals.list_unevaluated_paper_proposal_ids(
            limit=batch_size
        )
        await session.rollback()  # read-only pass; nothing to commit
    return candidate_ids


async def run_poll_cycle(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    id_generator: IdGenerator,
    clock: Clock,
    batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
) -> bool:
    """One claim-and-execute pass over up to `batch_size` candidates.
    Returns `True` if at least one candidate `proposal_id` was found
    (regardless of whether execution ultimately won the claim race for it),
    `False` if the queue was empty - `run_poll_loop` uses this to decide
    whether to sleep before polling again."""
    candidate_ids = await _list_candidate_proposal_ids(session_factory, batch_size=batch_size)
    if not candidate_ids:
        return False

    for proposal_id in candidate_ids:
        try:
            await run_once(session_factory, proposal_id, id_generator=id_generator, clock=clock)
        except ProposalNotFoundError:
            _logger.warning("candidate_proposal_vanished_before_claim", proposal_id=proposal_id)
        except (RiskConfigUnavailableError, CashLedgerUnavailableError) as exc:
            _logger.error(
                "proposal_left_unevaluated",
                proposal_id=proposal_id,
                reason=exc.__class__.__name__,
            )
    return True


async def run_poll_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    id_generator: IdGenerator,
    clock: Clock,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    batch_size: int = DEFAULT_CLAIM_BATCH_SIZE,
    max_iterations: int | None = None,
) -> None:
    """The DB-polled claim loop (ADR-011). `max_iterations` is test-only -
    production callers leave it `None` and run until the process is
    stopped."""
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        found_any = await run_poll_cycle(
            session_factory, id_generator=id_generator, clock=clock, batch_size=batch_size
        )
        iterations += 1
        if not found_any:
            await asyncio.sleep(poll_interval_seconds)
