"""Proposal-writing boundary for `atp_strategy` (ADR-014 §B/§E, ADR-015,
Milestone 2C).

Converts a `Strategy`'s `ProposedTrade` into a real `TradeProposal` and
persists it through the existing intake shape (mirrors ADR-012's
server-set-fields pattern): `mode` is always forced to `PAPER`,
`strategy_id`/`strategy_version` attribute the row (ADR-015), `created_by`
is always `None`, `proposal_id`/`created_at` are minted from the injected
`IdGenerator`/`Clock`.

`client_request_id` is derived deterministically here, in the platform
layer - never supplied by the strategy (the Milestone 2C amendment to
`atp_domain.strategy.ProposedTrade` removes that field entirely, see that
module's docstring). Quantizing `as_of` to a cycle-epoch bucket (the same
integer-arithmetic shape `atp_worker.scheduler` already uses for window
selection) is what makes the key stable across repeated evaluation within
one cycle, while a new cycle always derives a new one.

`atp_strategy` holds INSERT-only access to `paper.trade_proposals`
(ADR-015) - no `SELECT` grant exists to detect a duplicate
`client_request_id` by reading it back, unlike
`atp_api.services.paper_proposals.submit_proposal`, which refetches on
`IntegrityError`. Here a duplicate is simply an idempotent replay: catch
`IntegrityError`, log it, move on - never re-raise, never refetch.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError

from atp_domain.audit import ACTION_PROPOSAL_CREATED, AuditEvent
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.proposals import TradeProposal
from atp_domain.strategy import ProposedTrade, Strategy, derive_strategy_id
from atp_domain.types import ActorType, EventId, Mode, ProposalId
from atp_platform.logging import get_logger
from atp_strategy.uow import StrategyUnitOfWork, UnitOfWorkFactory

_logger = get_logger("atp_strategy.proposals")


def resolve_cycle_epoch(as_of: datetime, *, evaluation_interval_seconds: float) -> int:
    """Quantizes `as_of` to the evaluation-interval bucket it falls in -
    the same integer-arithmetic shape `atp_worker.scheduler` already uses
    for window selection. Re-evaluating within the same bucket derives an
    identical `client_request_id`; the next bucket derives a new one."""
    interval_seconds = int(evaluation_interval_seconds)
    epoch_seconds = int(as_of.timestamp())
    return (epoch_seconds // interval_seconds) * interval_seconds


def derive_client_request_id(
    *,
    strategy_key: str,
    strategy_version: int,
    cycle_epoch: int,
    instrument_key: str,
    ordinal: int,
) -> str:
    """Deterministic, never random: the same `(strategy_key,
    strategy_version, cycle_epoch, instrument_key, ordinal)` tuple always
    derives the same id, which is exactly what
    `UNIQUE(client_request_id)` needs to treat repeated evaluation within
    one cycle as a replay rather than a fresh proposal. `instrument_key`
    is a plain opaque string component of the composite key - not `
    instrument_id`, deliberately: safety invariant #19's parameter-name
    scan treats `instrument_id` as forbidden on any public function, to
    keep every such signature explicit about not passing through a raw
    identifier a caller could substitute execution parameters through."""
    return f"{strategy_key}:v{strategy_version}:{cycle_epoch}:{instrument_key}:{ordinal}"


def build_trade_proposal(
    proposed: ProposedTrade,
    *,
    strategy: Strategy,
    client_request_id: str,
    proposal_id: ProposalId,
    created_at: datetime,
) -> TradeProposal:
    """Pure construction - no I/O. `created_by` is always `None`: a
    strategy-authored proposal has no `core.users` row (ADR-015)."""
    return TradeProposal(
        proposal_id=proposal_id,
        mode=Mode.PAPER,
        instrument_id=proposed.instrument_id,
        side=proposed.side,
        quantity=proposed.quantity,
        order_type=proposed.order_type,
        limit_price=proposed.limit_price,
        trigger_price=None,
        product=proposed.product,
        client_request_id=client_request_id,
        created_at=created_at,
        strategy_id=derive_strategy_id(strategy.strategy_key),
        strategy_version=strategy.strategy_version,
        expected_risk=proposed.expected_risk,
    )


async def write_proposal(
    uow: StrategyUnitOfWork,
    proposal: TradeProposal,
    *,
    strategy_key: str,
    id_generator: IdGenerator,
    clock: Clock,
    correlation_id: str,
) -> None:
    """Pure persistence logic against an already-open `StrategyUnitOfWork`
    - no transaction management of its own, fully unit-testable with a
    fake `uow`. Saves the `TradeProposal` and its `ACTION_PROPOSAL_CREATED`
    audit event together; a duplicate `client_request_id` raises
    `sqlalchemy.exc.IntegrityError` (`UNIQUE(client_request_id)`), which
    this function does **not** catch - `persist_proposed_trade` below is
    the layer that translates that into a replay, exactly as
    `atp_exec_paper.gateway.execute_proposal`/`run_once` split the same
    responsibility."""
    now = clock.now()
    await uow.trade_proposals.save(proposal, created_by=None)
    await uow.audit.save(
        AuditEvent(
            event_id=EventId(id_generator.new_id()),
            correlation_id=correlation_id,
            occurred_at=now,
            recorded_at=now,
            actor_type=ActorType.AGENT,
            actor_id=f"strategy/{strategy_key}",
            action=ACTION_PROPOSAL_CREATED,
            mode=Mode.PAPER,
            strategy_id=proposal.strategy_id,
            strategy_version=proposal.strategy_version,
            instrument_id=proposal.instrument_id,
            decision=None,
        )
    )


async def persist_proposed_trade(
    uow_factory: UnitOfWorkFactory,
    proposal: TradeProposal,
    *,
    strategy_key: str,
    id_generator: IdGenerator,
    clock: Clock,
    correlation_id: str,
) -> bool:
    """Opens its own transaction (`uow_factory`), runs `write_proposal`,
    and translates a duplicate `client_request_id` (`IntegrityError`,
    after the transaction has already been rolled back by the `uow_factory`
    context manager) into `False` rather than letting it surface as an
    unhandled error - mirrors `atp_exec_paper.gateway.run_once`'s own
    `IntegrityError` translation exactly. `atp_strategy` holds no `SELECT`
    grant on `paper.trade_proposals` (ADR-015), so unlike
    `atp_api.services.paper_proposals.submit_proposal` this never refetches
    the existing row - a replay is simply skipped, independent of every
    other proposal from the same evaluation cycle (Milestone 2C §D)."""
    try:
        async with uow_factory() as uow:
            await write_proposal(
                uow,
                proposal,
                strategy_key=strategy_key,
                id_generator=id_generator,
                clock=clock,
                correlation_id=correlation_id,
            )
    except IntegrityError:
        _logger.info(
            "strategy_proposal_replay_skipped",
            client_request_id=proposal.client_request_id,
            strategy_key=strategy_key,
        )
        return False
    return True
