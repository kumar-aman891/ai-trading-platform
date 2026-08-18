"""`/healthz` and `/readyz` response shapes.

Deliberately minimal: neither ever carries a dependency name, a hostname,
or an exception message - see atp_api.errors and
atp_api.services.dependencies for where that boundary is enforced.
"""

from __future__ import annotations

from typing import Literal

from atp_api.schemas.common import ApiModel


class LivenessResponse(ApiModel):
    status: Literal["OK"]


class ReadinessResponse(ApiModel):
    status: Literal["OK", "FAIL"]
    reason: str | None = None
