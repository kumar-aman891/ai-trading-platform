"""`GET /api/v1/instruments` response shape (Phase 1 Step 10).

`lot_size`/`tick_size` are exposed so a caller can construct a valid
`POST /api/v1/paper/proposals` `quantity` without guessing - the same
values `atp_exec_paper.risk_runner` reads to evaluate `RISK.ORDER.001`
(lot/tick rules). `tick_size` is a `str`, not a JSON number, matching
every other money/quantity field this API exposes (`schemas.paper`) - a
`Decimal` round-trips exactly through `str()`; a JSON float would not.
"""

from __future__ import annotations

from atp_api.schemas.common import ApiModel


class InstrumentResponse(ApiModel):
    instrument_id: str
    symbol: str
    name: str
    exchange: str
    segment: str
    lot_size: int
    tick_size: str


class InstrumentListResponse(ApiModel):
    items: list[InstrumentResponse]
