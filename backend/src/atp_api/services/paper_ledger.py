"""`GET /api/v1/paper/{proposals,positions,cash}` application logic -
read-only (Phase 1 Step 10).

Every proposal, its (at most one) `RiskDecision`, its (at most one) `Order`,
and its (at most one) `Fill` are assembled here purely by reading what
`atp_exec_paper` has already written (ADR-011) - this module never
evaluates risk, never mints an intent, and never writes anything. A
proposal with no `decision` yet is exactly what `atp_exec_paper`'s claim
loop has not reached yet; it is not an error.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from atp_domain.proposals import TradeProposal
from atp_domain.risk.engine import RiskDecision
from atp_domain.types import Mode, OrderId, ProposalId
from atp_persistence.repositories import (
    SqlAlchemyCashLedgerRepository,
    SqlAlchemyFillRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyPositionRepository,
    SqlAlchemyRiskDecisionRepository,
    SqlAlchemyTradeProposalRepository,
)

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class RuleResultView:
    rule_id: str
    outcome: str
    message: str


@dataclass(frozen=True, slots=True)
class RiskDecisionView:
    decision_id: str
    outcome: str
    rule_results: tuple[RuleResultView, ...]
    decided_at: datetime


@dataclass(frozen=True, slots=True)
class OrderView:
    internal_order_id: str
    status: str
    submitted_at: datetime
    last_update_at: datetime


@dataclass(frozen=True, slots=True)
class FillView:
    fill_id: str
    quantity: Decimal
    price: Decimal
    fees: Decimal
    taxes: Decimal
    simulated: bool
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class ProposalView:
    proposal_id: str
    mode: str
    instrument_id: str
    side: str
    quantity: Decimal
    order_type: str
    limit_price: Decimal | None
    product: str
    client_request_id: str
    created_at: datetime
    decision: RiskDecisionView | None
    order: OrderView | None
    fill: FillView | None


@dataclass(frozen=True, slots=True)
class ProposalPageView:
    items: tuple[ProposalView, ...]
    next_before: datetime | None
    limit: int


@dataclass(frozen=True, slots=True)
class PositionView:
    position_id: str
    instrument_id: str
    quantity: Decimal
    average_price: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    updated_at: datetime


def _decision_view(decision: RiskDecision) -> RiskDecisionView:
    return RiskDecisionView(
        decision_id=str(decision.decision_id),
        outcome=decision.outcome.value,
        rule_results=tuple(
            RuleResultView(rule_id=r.rule_id, outcome=r.outcome.value, message=r.message)
            for r in decision.rule_results
        ),
        decided_at=decision.decided_at,
    )


async def _build_proposal_view(
    proposal: TradeProposal,
    *,
    risk_decisions: SqlAlchemyRiskDecisionRepository,
    orders: SqlAlchemyOrderRepository,
    fills: SqlAlchemyFillRepository,
) -> ProposalView:
    decision = await risk_decisions.get_by_proposal(proposal.proposal_id)
    order = await orders.get_by_proposal(proposal.proposal_id)

    order_view: OrderView | None = None
    fill_view: FillView | None = None
    if order is not None:
        order_view = OrderView(
            internal_order_id=str(order.internal_order_id),
            status=order.status.value,
            submitted_at=order.submitted_at,
            last_update_at=order.last_update_at,
        )
        matching_fills = await fills.list_by_order(OrderId(order.internal_order_id))
        if matching_fills:
            # Phase 1's simulator produces at most one fill per order (no
            # partial fills, fill.md) - the first (only) fill is the whole
            # picture.
            fill = matching_fills[0]
            fill_view = FillView(
                fill_id=str(fill.fill_id),
                quantity=fill.quantity.value,
                price=fill.price.value,
                fees=fill.fees.value,
                taxes=fill.taxes.value,
                simulated=fill.simulated,
                filled_at=fill.filled_at,
            )

    return ProposalView(
        proposal_id=str(proposal.proposal_id),
        mode=proposal.mode.value,
        instrument_id=str(proposal.instrument_id),
        side=proposal.side.value,
        quantity=proposal.quantity.value,
        order_type=proposal.order_type.value,
        limit_price=proposal.limit_price.value if proposal.limit_price is not None else None,
        product=proposal.product.value,
        client_request_id=proposal.client_request_id,
        created_at=proposal.created_at,
        decision=_decision_view(decision) if decision is not None else None,
        order=order_view,
        fill=fill_view,
    )


async def get_proposal_detail(
    proposal_id: str,
    *,
    trade_proposals: SqlAlchemyTradeProposalRepository,
    risk_decisions: SqlAlchemyRiskDecisionRepository,
    orders: SqlAlchemyOrderRepository,
    fills: SqlAlchemyFillRepository,
) -> ProposalView | None:
    proposal = await trade_proposals.get(ProposalId(proposal_id))
    if proposal is None:
        return None
    return await _build_proposal_view(
        proposal, risk_decisions=risk_decisions, orders=orders, fills=fills
    )


async def list_proposals(
    *,
    trade_proposals: SqlAlchemyTradeProposalRepository,
    risk_decisions: SqlAlchemyRiskDecisionRepository,
    orders: SqlAlchemyOrderRepository,
    fills: SqlAlchemyFillRepository,
    mode: Mode,
    before: datetime | None,
    limit: int,
) -> ProposalPageView:
    bounded_limit = max(1, min(limit, MAX_PAGE_SIZE))
    proposals: Sequence[TradeProposal] = await trade_proposals.list_for_mode(
        mode, before=before, limit=bounded_limit
    )
    items = tuple(
        [
            await _build_proposal_view(
                proposal, risk_decisions=risk_decisions, orders=orders, fills=fills
            )
            for proposal in proposals
        ]
    )
    next_before = items[-1].created_at if len(items) == bounded_limit else None
    return ProposalPageView(items=items, next_before=next_before, limit=bounded_limit)


async def list_positions(
    repository: SqlAlchemyPositionRepository, *, mode: Mode
) -> tuple[PositionView, ...]:
    positions = await repository.list_all(mode)
    return tuple(
        PositionView(
            position_id=str(p.position_id),
            instrument_id=str(p.instrument_id),
            quantity=p.quantity,
            average_price=p.average_price.value if p.average_price is not None else None,
            realized_pnl=p.realized_pnl.value,
            unrealized_pnl=p.unrealized_pnl.value,
            updated_at=p.updated_at,
        )
        for p in positions
    )


async def get_cash_balance(
    repository: SqlAlchemyCashLedgerRepository, *, mode: Mode
) -> Decimal | None:
    return await repository.get_balance(mode)
