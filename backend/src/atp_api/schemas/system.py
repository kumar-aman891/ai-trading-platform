"""`GET /api/v1/system/status` response shape.

`mode` is `Literal["PAPER"]` deliberately, not `str` - Pydantic itself
refuses to serialize any other value, so a future bug that somehow
produced a non-PAPER mode value would fail loudly here rather than being
silently echoed to a caller (ADR-005, ADR-008: no code path constructs
Mode.LIVE). No request field anywhere feeds this value; it is read only
from server-side `Settings.trading_mode`.
"""

from __future__ import annotations

from typing import Literal

from atp_api.schemas.common import ApiModel


class DependencyHealth(ApiModel):
    name: str
    status: Literal["OK", "FAIL"]


class SystemStatusResponse(ApiModel):
    mode: Literal["PAPER"]
    version: str
    environment: str
    migration_version: str | None
    degraded: bool
    dependencies: list[DependencyHealth]
