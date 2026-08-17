"""Notification port. No implementation in Phase 1 - the fake in-memory
sink lives in test fixtures, not here."""

from __future__ import annotations

from typing import Protocol


class NotificationPort(Protocol):
    async def notify(self, *, subject: str, body: str, severity: str) -> None: ...
