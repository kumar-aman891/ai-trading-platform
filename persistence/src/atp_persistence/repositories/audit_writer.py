"""Write-only counterpart to `atp_persistence.repositories.audit_events`
(Phase 1 Step 8: authentication events need to be recorded).

Deliberately a *separate* class, not a `save` method added to
`atp_domain.ports.storage.AuditEventRepository` - that Protocol's own
docstring states "no route anywhere writes through this port" as a Step 7
design decision, and this module does not reopen or contradict it. Auth
services (`atp_api.services.auth`) depend on this concrete class directly,
the same way `atp_persistence.repositories.kill_switches` is depended on
directly without a domain Protocol.

Every write here happens inside the same `UnitOfWork` transaction as the
state change it records (`core.users`/`core.sessions` mutation), per
ADR-010 - `atp_persistence.db.UnitOfWork` attaches this alongside
`users`/`sessions`, sharing one `AsyncSession`/one commit.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.audit import AuditEvent
from atp_persistence.mappers import audit_event_to_row


class SqlAlchemyAuditEventWriter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, event: AuditEvent) -> None:
        self._session.add(audit_event_to_row(event))
