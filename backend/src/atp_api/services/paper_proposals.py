"""`POST /api/v1/paper/proposals` application logic - PAPER trade-proposal
intake (Phase 1 Step 10, ADR-012).

This module performs **structural validation only** and **never evaluates
risk**. It never calls `atp_domain.risk.engine.evaluate`/
`mint_intent_for_decision` and never imports `atp_domain.intents` at all
(`tests/safety/test_proposal_intake_is_not_a_risk_gate.py` asserts this
mechanically) and it is not gated on the kill switch - an engaged `PAPER`
kill switch instead produces a persisted, auditable `RiskDecision` with a
named rule id from `atp_exec_paper`, which is a strictly better trail than
a silent rejection at the door, and keeps exactly one authoritative risk
boundary (ADR-012 point 3).

`mode`, `proposal_id`, and `created_at` are always server-set here, never
accepted from a caller (ADR-012 point 4); `created_by` is the authenticated
principal's `user_id`, never a request field.

Idempotency (`docs/schemas/order.md`) is enforced by
`paper.trade_proposals`' own `UNIQUE (client_request_id)` constraint, not a
check-then-insert (which races - the Step 9 lesson, ADR-011): this function
always attempts the insert first, and only inspects the existing row after
a genuine `IntegrityError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from atp_api.errors import ConflictError, UnknownInstrumentError
from atp_domain.audit import ACTION_PROPOSAL_CREATED, AuditEvent
from atp_domain.clock import Clock
from atp_domain.ids import IdGenerator
from atp_domain.money import Price, Quantity
from atp_domain.proposals import TradeProposal
from atp_domain.types import (
    ActorType,
    EventId,
    InstrumentId,
    Mode,
    OrderType,
    Product,
    ProposalId,
    Side,
)
from atp_persistence.db import UnitOfWork
from atp_persistence.repositories import SqlAlchemyInstrumentRepository


@dataclass(frozen=True, slots=True)
class SubmitProposalResult:
    proposal_id: str
    client_request_id: str
    is_replay: bool


def _fields_match(
    existing: TradeProposal,
    *,
    instrument_id: str,
    side: Side,
    quantity: Decimal,
    order_type: OrderType,
    limit_price: Decimal | None,
    product: Product,
) -> bool:
    existing_limit_price = existing.limit_price.value if existing.limit_price is not None else None
    return (
        str(existing.instrument_id) == instrument_id
        and existing.side is side
        and existing.quantity.value == quantity
        and existing.order_type is order_type
        and existing_limit_price == limit_price
        and existing.product is product
    )


async def submit_proposal(
    uow: UnitOfWork,
    instrument_repository: SqlAlchemyInstrumentRepository,
    *,
    instrument_id: str,
    side: str,
    quantity: Decimal,
    order_type: str,
    limit_price: Decimal | None,
    product: str,
    client_request_id: str,
    expected_risk: dict[str, object],
    created_by: str,
    correlation_id: str,
    clock: Clock,
    id_generator: IdGenerator,
) -> SubmitProposalResult:
    if await instrument_repository.get(instrument_id) is None:
        raise UnknownInstrumentError()

    side_value = Side(side)
    order_type_value = OrderType(order_type)
    product_value = Product(product)
    now = clock.now()

    proposal = TradeProposal(
        proposal_id=ProposalId(id_generator.new_id()),
        mode=Mode.PAPER,
        instrument_id=InstrumentId(instrument_id),
        side=side_value,
        quantity=Quantity(quantity),
        order_type=order_type_value,
        limit_price=Price(limit_price) if limit_price is not None else None,
        trigger_price=None,
        product=product_value,
        client_request_id=client_request_id,
        created_at=now,
        strategy_id=None,
        strategy_version=None,
        source_signal_id=None,
        expected_risk=expected_risk,
    )

    try:
        await uow.trade_proposals.save(proposal, created_by=created_by)
    except IntegrityError:
        # The proposal's own fields (Quantity>0, LIMIT<->limit_price
        # coherence, ...) are already validated above by TradeProposal's
        # own __post_init__ before this insert is ever attempted, so an
        # IntegrityError reaching this point is `client_request_id`'s
        # UNIQUE constraint, not a CHECK failure.
        await uow.rollback()
        existing = await uow.trade_proposals.get_by_client_request_id(client_request_id)
        if existing is not None and _fields_match(
            existing,
            instrument_id=instrument_id,
            side=side_value,
            quantity=quantity,
            order_type=order_type_value,
            limit_price=limit_price,
            product=product_value,
        ):
            return SubmitProposalResult(
                proposal_id=str(existing.proposal_id),
                client_request_id=existing.client_request_id,
                is_replay=True,
            )
        raise ConflictError() from None

    await uow.audit.save(
        AuditEvent(
            event_id=EventId(id_generator.new_id()),
            correlation_id=correlation_id,
            occurred_at=now,
            recorded_at=now,
            actor_type=ActorType.USER,
            actor_id=created_by,
            action=ACTION_PROPOSAL_CREATED,
            mode=Mode.PAPER,
            strategy_id=None,
            strategy_version=None,
            instrument_id=proposal.instrument_id,
            decision=None,
        )
    )

    return SubmitProposalResult(
        proposal_id=str(proposal.proposal_id),
        client_request_id=proposal.client_request_id,
        is_replay=False,
    )
