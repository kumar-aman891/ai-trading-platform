"""Strategy evaluation runner (ADR-014, ADR-015, Milestone 2C).

Mirrors `atp_exec_paper.gateway`'s `run_once`/`run_poll_cycle`/
`run_poll_loop` shape, adapted to strategy evaluation instead of proposal
execution - there is no "claim one item" concept here (a `StrategyRegistry`
is a fixed, in-memory set, not a queue), so `run_once` evaluates every
eligible registered strategy for one cycle rather than a single item.

Every function that opens a transaction takes an explicit
`atp_strategy.uow.UnitOfWorkFactory` parameter (mirroring
`atp_worker.runner`'s established shape, not `atp_exec_paper.gateway`'s
raw `session_factory`) - naming the seam is what makes "each proposal gets
its own independent transaction" visible in these signatures, and what
lets every function here be tested with a fake `uow_factory`, no real
database required.

Four transaction boundaries per cycle (deliberately not one):
1. **Context + kill-switch read** - one transaction
   (`_load_cycle_inputs`), read-only in practice (neither operation
   writes), so the `uow_factory`'s normal clean-exit commit is a no-op.
2. **Evaluation** - no transaction at all. `Strategy.evaluate()` runs with
   no `uow`/session in scope, which is what makes "one strategy's bug
   cannot roll back another strategy's proposal" true structurally, not by
   discipline.
3. **Each `ProposedTrade`** - its own short transaction
   (`atp_strategy.proposals.persist_proposed_trade`), containing the
   `TradeProposal` INSERT and its audit event together, independent of
   every other proposal from the same cycle.
4. **Failure** (a raising strategy, or a failed proposal write) - no
   transaction; caught, logged, counted, and evaluation continues.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.killswitch import (
    SwitchId,
    SwitchScope,
    SwitchState,
    is_blocking,
    resolve_switch_state,
)
from atp_domain.strategy import ProposedTrade, Strategy, StrategyContext, StrategyRegistry
from atp_domain.types import ProposalId
from atp_platform.correlation import new_correlation_id
from atp_platform.logging import get_logger
from atp_platform.metrics import counter
from atp_strategy.context import build_strategy_context
from atp_strategy.kill_switch_adapter import load_kill_switch_states
from atp_strategy.proposals import (
    build_trade_proposal,
    derive_client_request_id,
    persist_proposed_trade,
    resolve_cycle_epoch,
)
from atp_strategy.uow import UnitOfWorkFactory

_logger = get_logger("atp_strategy.runner")

DEFAULT_EVALUATION_INTERVAL_SECONDS = 60.0

_STRATEGY_EVALUATIONS = counter(
    "atp_strategy_evaluations_total",
    "Strategy.evaluate() attempts, by strategy_key and outcome (succeeded/failed).",
    labelnames=("strategy_key", "outcome"),
)
_STRATEGY_KILL_SWITCH_SKIPS = counter(
    "atp_strategy_kill_switch_skips_total",
    "Strategy evaluations skipped this cycle because STRATEGY:{key} resolved to blocking.",
    labelnames=("strategy_key",),
)
_PROPOSAL_WRITES = counter(
    "atp_strategy_proposal_writes_total",
    "TradeProposal persistence attempts, by strategy_key and outcome (written/replay/failed).",
    labelnames=("strategy_key", "outcome"),
)
_CYCLE_CONTEXT_LOAD_FAILURES = counter(
    "atp_strategy_cycle_context_load_failures_total",
    "Cycles abandoned before any strategy was evaluated because context/instrument loading raised.",
)


async def _load_cycle_inputs(
    uow_factory: UnitOfWorkFactory,
    *,
    clock: Clock,
    correlation_id: str,
) -> tuple[StrategyContext, Mapping[SwitchId, SwitchState]]:
    """One transaction per cycle: instrument/quote context and kill-switch
    state, loaded together. Read-only in practice (neither operation
    writes), so the `uow_factory` context manager's normal clean-exit
    commit is a no-op - matching how `atp_worker.scheduler` also lets a
    read-only-in-practice block commit normally rather than special-casing
    a rollback."""
    async with uow_factory() as uow:
        context = await build_strategy_context(
            uow, as_of=clock.now(), correlation_id=correlation_id
        )
        kill_switch_states = await load_kill_switch_states(uow.kill_switches)
    return context, kill_switch_states


async def _persist_proposed_trades(
    proposed_trades: Sequence[ProposedTrade],
    *,
    strategy: Strategy,
    context: StrategyContext,
    uow_factory: UnitOfWorkFactory,
    id_generator: IdGenerator,
    clock: Clock,
    evaluation_interval_seconds: float,
) -> None:
    cycle_epoch = resolve_cycle_epoch(
        context.as_of, evaluation_interval_seconds=evaluation_interval_seconds
    )
    for ordinal, proposed in enumerate(proposed_trades):
        try:
            client_request_id = derive_client_request_id(
                strategy_key=strategy.strategy_key,
                strategy_version=strategy.strategy_version,
                cycle_epoch=cycle_epoch,
                instrument_key=str(proposed.instrument_id),
                ordinal=ordinal,
            )
            trade_proposal = build_trade_proposal(
                proposed,
                strategy=strategy,
                client_request_id=client_request_id,
                proposal_id=ProposalId(id_generator.new_id()),
                created_at=context.as_of,
            )
            inserted = await persist_proposed_trade(
                uow_factory,
                trade_proposal,
                strategy_key=strategy.strategy_key,
                id_generator=id_generator,
                clock=clock,
                correlation_id=context.correlation_id,
            )
        except Exception:
            _logger.exception(
                "strategy_proposal_persist_failed",
                strategy_key=strategy.strategy_key,
                ordinal=ordinal,
            )
            _PROPOSAL_WRITES.labels(strategy_key=strategy.strategy_key, outcome="failed").inc()
            continue

        outcome = "written" if inserted else "replay"
        _PROPOSAL_WRITES.labels(strategy_key=strategy.strategy_key, outcome=outcome).inc()


async def _evaluate_and_persist_strategy(
    strategy: Strategy,
    context: StrategyContext,
    *,
    uow_factory: UnitOfWorkFactory,
    id_generator: IdGenerator,
    clock: Clock,
    evaluation_interval_seconds: float,
) -> None:
    try:
        proposed_trades = strategy.evaluate(context)  # pure - no I/O, no transaction open
    except Exception:
        _logger.exception("strategy_evaluation_failed", strategy_key=strategy.strategy_key)
        _STRATEGY_EVALUATIONS.labels(strategy_key=strategy.strategy_key, outcome="failed").inc()
        return

    _STRATEGY_EVALUATIONS.labels(strategy_key=strategy.strategy_key, outcome="succeeded").inc()
    if not proposed_trades:
        return

    await _persist_proposed_trades(
        proposed_trades,
        strategy=strategy,
        context=context,
        uow_factory=uow_factory,
        id_generator=id_generator,
        clock=clock,
        evaluation_interval_seconds=evaluation_interval_seconds,
    )


async def run_once(
    uow_factory: UnitOfWorkFactory,
    *,
    registry: StrategyRegistry,
    id_generator: IdGenerator,
    clock: Clock,
    evaluation_interval_seconds: float = DEFAULT_EVALUATION_INTERVAL_SECONDS,
) -> bool:
    """One evaluation cycle over every strategy in `registry`. Returns
    `False` if nothing was registered or the cycle's context could not be
    loaded (fail-closed: no strategy is evaluated without a freshly loaded
    context), `True` otherwise."""
    strategies = registry.all()
    if not strategies:
        return False

    correlation_id = new_correlation_id()
    try:
        context, kill_switch_states = await _load_cycle_inputs(
            uow_factory, clock=clock, correlation_id=correlation_id
        )
    except Exception:
        _logger.exception("strategy_cycle_context_load_failed", correlation_id=correlation_id)
        _CYCLE_CONTEXT_LOAD_FAILURES.inc()
        return False

    for strategy in strategies:
        switch_id = SwitchId(scope=SwitchScope.STRATEGY, qualifier=strategy.strategy_key)
        state = resolve_switch_state(switch_id, kill_switch_states)
        if is_blocking(state):
            _logger.info(
                "strategy_skipped_kill_switch_blocking",
                strategy_key=strategy.strategy_key,
                state=str(state),
            )
            _STRATEGY_KILL_SWITCH_SKIPS.labels(strategy_key=strategy.strategy_key).inc()
            continue

        await _evaluate_and_persist_strategy(
            strategy,
            context,
            uow_factory=uow_factory,
            id_generator=id_generator,
            clock=clock,
            evaluation_interval_seconds=evaluation_interval_seconds,
        )

    return True


async def run_poll_cycle(
    uow_factory: UnitOfWorkFactory,
    *,
    registry: StrategyRegistry,
    id_generator: IdGenerator,
    clock: Clock,
    evaluation_interval_seconds: float = DEFAULT_EVALUATION_INTERVAL_SECONDS,
) -> bool:
    """Wraps `run_once` - mirrors `atp_worker.runner.run_poll_cycle`'s
    two-layer shape (lease sweep + claim there; nothing to sweep here, no
    lease concept applies to a fixed in-memory registry), kept as a
    distinct function so `run_poll_loop` never needs to change if a future
    milestone adds cycle-level bookkeeping."""
    return await run_once(
        uow_factory,
        registry=registry,
        id_generator=id_generator,
        clock=clock,
        evaluation_interval_seconds=evaluation_interval_seconds,
    )


async def run_poll_loop(
    uow_factory: UnitOfWorkFactory,
    *,
    registry: StrategyRegistry,
    id_generator: IdGenerator,
    clock: Clock,
    evaluation_interval_seconds: float = DEFAULT_EVALUATION_INTERVAL_SECONDS,
    max_iterations: int | None = None,
) -> None:
    """Unlike `atp_exec_paper.gateway.run_poll_loop`/`atp_worker.runner
    .run_poll_loop` (which sleep only when a poll finds nothing to drain),
    this loop always sleeps: a `StrategyRegistry` is not a queue, so there
    is nothing to drain back-to-back - every iteration evaluates the same
    fixed set of strategies, at most once per `evaluation_interval_seconds`."""
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        # run_once/run_poll_cycle already catch every exception they can
        # attribute to a specific strategy or proposal; this is a last-
        # resort backstop so a genuinely unexpected failure (e.g. the
        # session factory itself misconfigured) logs and yields the next
        # iteration rather than crashing the process, mirroring
        # atp_worker.runner.run_poll_loop's `except WorkerError` backstop.
        try:
            await run_poll_cycle(
                uow_factory,
                registry=registry,
                id_generator=id_generator,
                clock=clock,
                evaluation_interval_seconds=evaluation_interval_seconds,
            )
        except Exception:
            _logger.exception("strategy_poll_cycle_failed")
        iterations += 1
        if max_iterations is None or iterations < max_iterations:
            await asyncio.sleep(evaluation_interval_seconds)
