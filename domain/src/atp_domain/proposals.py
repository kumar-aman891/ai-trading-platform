"""TradeProposal - the first typed artifact in the execution flow:
AI/strategy -> TradeProposal -> RiskDecision -> ApprovedOrderIntent ->
ExecutionGateway -> Broker (docs/ARCHITECTURE.md §2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from atp_domain.errors import InvalidTradeProposalError
from atp_domain.money import Price, Quantity
from atp_domain.types import (
    InstrumentId,
    Mode,
    OrderType,
    Product,
    ProposalId,
    Side,
    SignalId,
    StrategyId,
)


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None:
        raise InvalidTradeProposalError(f"{field_name} must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class TradeProposal:
    proposal_id: ProposalId
    mode: Mode
    instrument_id: InstrumentId
    side: Side
    quantity: Quantity
    order_type: OrderType
    limit_price: Price | None
    trigger_price: Price | None
    product: Product
    client_request_id: str
    created_at: datetime
    strategy_id: StrategyId | None = None
    strategy_version: int | None = None
    source_signal_id: SignalId | None = None
    expected_risk: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        _require_aware(self.created_at, field_name="created_at")

        if not self.client_request_id.strip():
            raise InvalidTradeProposalError("client_request_id must not be empty.")

        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise InvalidTradeProposalError("LIMIT orders require a limit_price.")
        if self.order_type is OrderType.MARKET and self.limit_price is not None:
            raise InvalidTradeProposalError("MARKET orders must not carry a limit_price.")

        if self.strategy_id is None and self.strategy_version is not None:
            raise InvalidTradeProposalError("strategy_version requires strategy_id.")

        if not isinstance(self.expected_risk, MappingProxyType):
            object.__setattr__(self, "expected_risk", MappingProxyType(dict(self.expected_risk)))
