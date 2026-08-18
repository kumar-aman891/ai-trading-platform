"""`/api/v1/auth/*` request/response DTOs.

`LoginRequest` never has a `role`/`permission`/`mode` field - a client
cannot request a role or elevate one via the login body (RBAC role comes
only from `core.users.role`, looked up server-side after authentication).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from atp_api.schemas.common import ApiModel


class LoginRequest(ApiModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class LoginResponse(ApiModel):
    username: str
    role: Literal["viewer", "researcher", "paper_trader", "live_trader", "administrator"]
    must_change_password: bool
    expires_at: datetime


class MeResponse(ApiModel):
    username: str
    role: Literal["viewer", "researcher", "paper_trader", "live_trader", "administrator"]
    must_change_password: bool


class MessageResponse(ApiModel):
    message: str
