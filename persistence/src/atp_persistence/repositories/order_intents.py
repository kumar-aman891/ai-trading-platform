"""Concrete implementation of `atp_domain.ports.storage.OrderIntentRepository`
(ADR-008, ADR-011). Single-use by the database's own `UNIQUE (decision_id)`
constraint on `paper.order_intents` - this class does not pre-check
uniqueness itself; a caller inserting a second intent for the same decision
gets an `IntegrityError` from the database, not a silent overwrite.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_domain.intents import ApprovedOrderIntent
from atp_domain.types import DecisionId
from atp_persistence.mappers import order_intent_to_row
from atp_persistence.models.paper import OrderIntentRow


class SqlAlchemyOrderIntentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, intent: ApprovedOrderIntent) -> None:
        self._session.add(order_intent_to_row(intent))
        await self._session.flush()

    async def exists_for_decision(self, decision_id: DecisionId) -> bool:
        result = await self._session.execute(
            select(OrderIntentRow.intent_id).where(OrderIntentRow.decision_id == str(decision_id))
        )
        return result.scalar_one_or_none() is not None
