"""Core enums and stable typed identifiers.

Identifiers are `NewType` wrappers over `str` (a UUIDv7 string, minted via
`atp_domain.ids.IdGenerator`) — this keeps a `ProposalId` from being
accidentally interchangeable with an `OrderId` at the type-checker level,
without introducing any runtime wrapper class or serialization concern.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NewType


class Mode(StrEnum):
    """PAPER is the only mode with any execution path in Phase 1. LIVE
    exists as a value so the type system can represent it (a RiskConfig,
    a TradeProposal, an Order all carry a `mode` field) — but no code path
    anywhere approves a LIVE proposal (ADR-005, ADR-008)."""

    PAPER = "PAPER"
    LIVE = "LIVE"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class Product(StrEnum):
    """Phase 1 subset of Kite product types (docs/schemas/trade_proposal.md)."""

    CNC = "CNC"
    MIS = "MIS"


class Segment(StrEnum):
    EQ = "EQ"
    FO = "FO"


class OrderStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ActorType(StrEnum):
    """Who/what took an action, for AuditEvent (docs/OBSERVABILITY.md)."""

    USER = "USER"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"
    BROKER = "BROKER"


class DecisionOutcome(StrEnum):
    """A RiskDecision's aggregate outcome - distinct from RuleOutcome
    (PASS/REJECT/INDETERMINATE), which is per-rule (docs/schemas/risk_decision.md)."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Typed identifiers
# ---------------------------------------------------------------------------

InstrumentId = NewType("InstrumentId", str)
StrategyId = NewType("StrategyId", str)
SignalId = NewType("SignalId", str)
ProposalId = NewType("ProposalId", str)
DecisionId = NewType("DecisionId", str)
RiskConfigId = NewType("RiskConfigId", str)
IntentId = NewType("IntentId", str)
OrderId = NewType("OrderId", str)
FillId = NewType("FillId", str)
PositionId = NewType("PositionId", str)
EventId = NewType("EventId", str)
