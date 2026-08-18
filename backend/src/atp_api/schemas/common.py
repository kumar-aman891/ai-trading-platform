"""Shared Pydantic base classes and the error envelope every route returns
on failure. Nothing here is an ORM model or a domain dataclass - the API
boundary only ever speaks these types (ADR-009's "three distinct types for
three distinct concerns" applied to the fourth layer FastAPI adds)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Base for every request/response DTO. `extra="forbid"` rejects
    unexpected fields on any model used as a request body - harmless, but
    consistently applied, on response models too."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ErrorResponse(ApiModel):
    code: str
    message: str
    correlation_id: str | None = None
