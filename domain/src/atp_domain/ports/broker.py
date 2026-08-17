"""Broker port - split into a read side and a write side.

BrokerExecutionPort.submit accepts exactly one thing: an
ApprovedOrderIntent. There is no overload, no alternate method, and no way
to pass raw order parameters - the type signature itself is the boundary
(ADR-008). No implementation of either protocol exists in Phase 1
(ADR-006): no Kite adapter, no MCP client, nothing that touches a network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from atp_domain.intents import ApprovedOrderIntent
from atp_domain.types import InstrumentId, Segment


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    """Shape finalized in Phase 2 when a real instrument-master loader
    exists. Minimal placeholder fields only."""

    instrument_id: InstrumentId
    provider: str
    provider_instrument_token: str
    exchange: str
    segment: Segment
    symbol: str


@dataclass(frozen=True, slots=True)
class BrokerPositionRecord:
    """Broker-reported position, as opposed to atp_domain.orders.Position
    (this platform's own accounting). Shape finalized in Phase 2+."""

    instrument_id: InstrumentId
    quantity: str
    average_price: str


@dataclass(frozen=True, slots=True)
class BrokerAck:
    broker_order_id: str
    broker_provider: str
    acknowledged_at: datetime


class BrokerReadPort(Protocol):
    """Read-only. Backed by the AI-facing Kite MCP once built (ADR-006) -
    order/GTT/modify/cancel tools are never registered on this path."""

    async def get_instruments(self) -> Sequence[InstrumentRecord]: ...
    async def get_positions(self) -> Sequence[BrokerPositionRecord]: ...


class BrokerExecutionPort(Protocol):
    """The only write surface to a broker, ever. No implementation exists
    in Phase 1 - execution/live/ is not created."""

    async def submit(self, intent: ApprovedOrderIntent) -> BrokerAck: ...
