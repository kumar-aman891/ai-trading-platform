"""`POST /api/v1/paper/proposals`, `GET /api/v1/paper/proposals[/{id}]`,
`GET /api/v1/paper/positions`, `GET /api/v1/paper/cash` (Phase 1 Step 10).

No route here evaluates risk, mints an `ApprovedOrderIntent`, or calls
`atp_exec_paper` directly - see `atp_api.services.paper_proposals`,
`atp_api.services.paper_ledger`, and `docs/adr/ADR-012-proposal-intake-is-
not-a-risk-gate.md`. No path here contains "order", "execute", or "/live"
(`tests/safety/test_no_execution_path_in_api.py`'s existing path-substring
ban) - order/fill state is nested inside the proposal-detail response
instead of a `/paper/orders` route.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from atp_api.deps import (
    AuthenticatedPrincipal,
    enforce_csrf,
    get_cash_ledger_repository,
    get_clock,
    get_current_principal,
    get_fill_repository,
    get_id_generator,
    get_instrument_repository,
    get_order_repository,
    get_position_repository,
    get_risk_decision_repository,
    get_trade_proposal_repository,
    get_unit_of_work,
    require_permission,
)
from atp_api.errors import NotFoundError
from atp_api.schemas.paper import (
    CashBalanceResponse,
    FillResponse,
    OrderResponse,
    PositionListResponse,
    PositionResponse,
    ProposalPage,
    ProposalResponse,
    RiskDecisionResponse,
    RuleResultResponse,
    SubmitProposalRequest,
    SubmitProposalResponse,
)
from atp_api.security.rbac import Permission
from atp_api.services import paper_ledger, paper_proposals
from atp_api.services.paper_ledger import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.types import Mode
from atp_persistence.db import UnitOfWork
from atp_persistence.repositories import (
    SqlAlchemyCashLedgerRepository,
    SqlAlchemyFillRepository,
    SqlAlchemyInstrumentRepository,
    SqlAlchemyOrderRepository,
    SqlAlchemyPositionRepository,
    SqlAlchemyRiskDecisionRepository,
    SqlAlchemyTradeProposalRepository,
)
from atp_platform.correlation import get_correlation_id, new_correlation_id

router = APIRouter(prefix="/api/v1/paper", tags=["paper"])


def _to_proposal_response(view: paper_ledger.ProposalView) -> ProposalResponse:
    decision = (
        RiskDecisionResponse(
            decision_id=view.decision.decision_id,
            outcome=view.decision.outcome,
            rule_results=[
                RuleResultResponse(rule_id=r.rule_id, outcome=r.outcome, message=r.message)
                for r in view.decision.rule_results
            ],
            decided_at=view.decision.decided_at,
        )
        if view.decision is not None
        else None
    )
    order = (
        OrderResponse(
            internal_order_id=view.order.internal_order_id,
            status=view.order.status,
            submitted_at=view.order.submitted_at,
            last_update_at=view.order.last_update_at,
        )
        if view.order is not None
        else None
    )
    fill = (
        FillResponse(
            fill_id=view.fill.fill_id,
            quantity=str(view.fill.quantity),
            price=str(view.fill.price),
            fees=str(view.fill.fees),
            taxes=str(view.fill.taxes),
            simulated=view.fill.simulated,
            filled_at=view.fill.filled_at,
        )
        if view.fill is not None
        else None
    )
    return ProposalResponse(
        proposal_id=view.proposal_id,
        mode=view.mode,  # type: ignore[arg-type]  # paper.trade_proposals CHECK guarantees membership
        instrument_id=view.instrument_id,
        side=view.side,  # type: ignore[arg-type]
        quantity=str(view.quantity),
        order_type=view.order_type,  # type: ignore[arg-type]
        limit_price=str(view.limit_price) if view.limit_price is not None else None,
        product=view.product,  # type: ignore[arg-type]
        client_request_id=view.client_request_id,
        created_at=view.created_at,
        decision=decision,
        order=order,
        fill=fill,
    )


@router.post(
    "/proposals",
    response_model=SubmitProposalResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(require_permission(Permission.SUBMIT_PAPER_PROPOSAL)),
        Depends(enforce_csrf),
    ],
)
async def submit_proposal(
    payload: SubmitProposalRequest,
    response: Response,
    principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_unit_of_work)],
    instrument_repository: Annotated[
        SqlAlchemyInstrumentRepository, Depends(get_instrument_repository)
    ],
    clock: Annotated[Clock, Depends(get_clock)],
    id_generator: Annotated[IdGenerator, Depends(get_id_generator)],
) -> SubmitProposalResponse:
    result = await paper_proposals.submit_proposal(
        uow,
        instrument_repository,
        instrument_id=payload.instrument_id,
        side=payload.side,
        quantity=payload.quantity,
        order_type=payload.order_type,
        limit_price=payload.limit_price,
        product=payload.product,
        client_request_id=payload.client_request_id,
        expected_risk=payload.expected_risk,
        created_by=principal.user_id,
        correlation_id=get_correlation_id() or new_correlation_id(),
        clock=clock,
        id_generator=id_generator,
    )
    if result.is_replay:
        response.status_code = status.HTTP_200_OK
    return SubmitProposalResponse(
        proposal_id=result.proposal_id,
        status="PENDING_EVALUATION",
        client_request_id=result.client_request_id,
    )


@router.get(
    "/proposals",
    response_model=ProposalPage,
    dependencies=[Depends(require_permission(Permission.READ_PAPER_LEDGER))],
)
async def list_proposals(
    trade_proposals: Annotated[
        SqlAlchemyTradeProposalRepository, Depends(get_trade_proposal_repository)
    ],
    risk_decisions: Annotated[
        SqlAlchemyRiskDecisionRepository, Depends(get_risk_decision_repository)
    ],
    orders: Annotated[SqlAlchemyOrderRepository, Depends(get_order_repository)],
    fills: Annotated[SqlAlchemyFillRepository, Depends(get_fill_repository)],
    before: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ProposalPage:
    page = await paper_ledger.list_proposals(
        trade_proposals=trade_proposals,
        risk_decisions=risk_decisions,
        orders=orders,
        fills=fills,
        mode=Mode.PAPER,
        before=before,
        limit=limit,
    )
    return ProposalPage(
        items=[_to_proposal_response(item) for item in page.items],
        next_before=page.next_before,
        limit=page.limit,
    )


@router.get(
    "/proposals/{proposal_id}",
    response_model=ProposalResponse,
    dependencies=[Depends(require_permission(Permission.READ_PAPER_LEDGER))],
)
async def get_proposal(
    proposal_id: str,
    trade_proposals: Annotated[
        SqlAlchemyTradeProposalRepository, Depends(get_trade_proposal_repository)
    ],
    risk_decisions: Annotated[
        SqlAlchemyRiskDecisionRepository, Depends(get_risk_decision_repository)
    ],
    orders: Annotated[SqlAlchemyOrderRepository, Depends(get_order_repository)],
    fills: Annotated[SqlAlchemyFillRepository, Depends(get_fill_repository)],
) -> ProposalResponse:
    view = await paper_ledger.get_proposal_detail(
        proposal_id,
        trade_proposals=trade_proposals,
        risk_decisions=risk_decisions,
        orders=orders,
        fills=fills,
    )
    if view is None:
        raise NotFoundError()
    return _to_proposal_response(view)


@router.get(
    "/positions",
    response_model=PositionListResponse,
    dependencies=[Depends(require_permission(Permission.READ_PAPER_LEDGER))],
)
async def list_positions(
    repository: Annotated[SqlAlchemyPositionRepository, Depends(get_position_repository)],
) -> PositionListResponse:
    positions = await paper_ledger.list_positions(repository, mode=Mode.PAPER)
    return PositionListResponse(
        items=[
            PositionResponse(
                position_id=p.position_id,
                instrument_id=p.instrument_id,
                quantity=str(p.quantity),
                average_price=str(p.average_price) if p.average_price is not None else None,
                realized_pnl=str(p.realized_pnl),
                unrealized_pnl=str(p.unrealized_pnl),
                updated_at=p.updated_at,
            )
            for p in positions
        ]
    )


@router.get(
    "/cash",
    response_model=CashBalanceResponse,
    dependencies=[Depends(require_permission(Permission.READ_PAPER_LEDGER))],
)
async def get_cash_balance(
    repository: Annotated[SqlAlchemyCashLedgerRepository, Depends(get_cash_ledger_repository)],
) -> CashBalanceResponse:
    balance = await paper_ledger.get_cash_balance(repository, mode=Mode.PAPER)
    return CashBalanceResponse(mode="PAPER", balance=str(balance) if balance is not None else None)
