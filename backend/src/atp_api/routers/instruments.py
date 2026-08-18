"""`GET /api/v1/instruments` - read-only (Phase 1 Step 10)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from atp_api.deps import get_instrument_repository, require_permission
from atp_api.schemas.instrument import InstrumentListResponse, InstrumentResponse
from atp_api.security.rbac import Permission
from atp_api.services.instruments import list_instruments
from atp_persistence.repositories import SqlAlchemyInstrumentRepository

router = APIRouter(prefix="/api/v1/instruments", tags=["instruments"])


@router.get(
    "",
    response_model=InstrumentListResponse,
    dependencies=[Depends(require_permission(Permission.READ_INSTRUMENTS))],
)
async def get_instruments(
    repository: Annotated[SqlAlchemyInstrumentRepository, Depends(get_instrument_repository)],
) -> InstrumentListResponse:
    items = await list_instruments(repository)
    return InstrumentListResponse(
        items=[
            InstrumentResponse(
                instrument_id=item.instrument_id,
                symbol=item.symbol,
                name=item.name,
                exchange=item.exchange,
                segment=item.segment,
                lot_size=item.lot_size,
                tick_size=str(item.tick_size),
            )
            for item in items
        ]
    )
