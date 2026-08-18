"""`POST /api/v1/paper/proposals` and `GET /api/v1/paper/{proposals,
positions,cash}` request/response DTOs (Phase 1 Step 10, ADR-012).

`SubmitProposalRequest` deliberately has no `mode`, `created_by`,
`proposal_id`, or `created_at` field - those four are always server-set
(`atp_api.services.paper_proposals`), never caller-supplied (ADR-012 point
4). It also has no `strategy_id`/`strategy_version`/`source_signal_id`/
`trigger_price` field - no strategy engine or signal engine exists yet
(docs/schemas/trade_proposal.md), and `trigger_price` is "unused in Phase 1
(no stop-order types yet)" per that same schema doc, so accepting it from a
caller would be accepting a field nothing downstream reads. Money/quantity
fields are typed `Decimal` on the way in (pydantic-core parses a JSON
number or string into an exact `Decimal`, never routing through a Python
`float` first) and `str` on the way out (`Decimal.__str__` round-trips
exactly; a JSON float would not) - the same "never float" rule
`atp_domain.money` enforces at the domain boundary, applied here at the API
boundary.

`SubmitProposalResponse.status` is always `"PENDING_EVALUATION"` - a 2xx
from this route means *recorded*, never *approved* (ADR-012 point 1); the
only way to learn whether a proposal was approved, rejected, or not yet
evaluated is `GET /api/v1/paper/proposals/{proposal_id}`.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from atp_api.schemas.common import ApiModel

_ClientRequestId = Field(min_length=1, max_length=255)


class SubmitProposalRequest(ApiModel):
    instrument_id: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: Decimal | None = None
    product: Literal["CNC", "MIS"]
    client_request_id: str = _ClientRequestId
    expected_risk: dict[str, Any] = Field(default_factory=dict)


class SubmitProposalResponse(ApiModel):
    proposal_id: str
    status: Literal["PENDING_EVALUATION"]
    client_request_id: str


class RuleResultResponse(ApiModel):
    rule_id: str
    outcome: str
    message: str


class RiskDecisionResponse(ApiModel):
    decision_id: str
    outcome: str
    rule_results: list[RuleResultResponse]
    decided_at: datetime


class OrderResponse(ApiModel):
    internal_order_id: str
    status: str
    submitted_at: datetime
    last_update_at: datetime


class FillResponse(ApiModel):
    fill_id: str
    quantity: str
    price: str
    fees: str
    taxes: str
    simulated: bool
    filled_at: datetime


class ProposalResponse(ApiModel):
    """`decision`/`order`/`fill` are `None` until `atp_exec_paper`'s
    claim loop (ADR-011) evaluates and, if approved, executes this
    proposal - never populated by this router itself."""

    proposal_id: str
    mode: Literal["PAPER"]
    instrument_id: str
    side: Literal["BUY", "SELL"]
    quantity: str
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: str | None
    product: Literal["CNC", "MIS"]
    client_request_id: str
    created_at: datetime
    decision: RiskDecisionResponse | None
    order: OrderResponse | None
    fill: FillResponse | None


class ProposalPage(ApiModel):
    items: list[ProposalResponse]
    next_before: datetime | None
    limit: int


class PositionResponse(ApiModel):
    position_id: str
    instrument_id: str
    quantity: str
    average_price: str | None
    realized_pnl: str
    unrealized_pnl: str
    updated_at: datetime


class PositionListResponse(ApiModel):
    items: list[PositionResponse]


class CashBalanceResponse(ApiModel):
    mode: Literal["PAPER"]
    balance: str | None
