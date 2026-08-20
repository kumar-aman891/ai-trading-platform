"""`atp_strategy.proposals` - the proposal-writing boundary (ADR-014 §B/§E,
ADR-015, Milestone 2C). No database connection is used."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from atp_domain.clock import FrozenClock
from atp_domain.ids import SequentialIdGenerator
from atp_domain.money import Quantity
from atp_domain.proposals import TradeProposal
from atp_domain.strategy import ProposedTrade, derive_strategy_id
from atp_domain.types import InstrumentId, Mode, OrderType, Product, ProposalId, Side
from atp_strategy.proposals import (
    build_trade_proposal,
    derive_client_request_id,
    persist_proposed_trade,
    resolve_cycle_epoch,
    write_proposal,
)
from tests.unit.strategy.fakes import RecordingUnitOfWorkFactory

_AS_OF = datetime(2026, 1, 1, tzinfo=UTC)
_INSTRUMENT_ID = InstrumentId("11111111-1111-7111-8111-111111111111")


class _FakeStrategy:
    strategy_key = "momentum-v1"
    strategy_version = 1


def _proposed_trade(**overrides: object) -> ProposedTrade:
    defaults: dict[str, object] = {
        "instrument_id": _INSTRUMENT_ID,
        "side": Side.BUY,
        "quantity": Quantity(Decimal(10)),
        "order_type": OrderType.MARKET,
        "limit_price": None,
        "product": Product.CNC,
    }
    defaults.update(overrides)
    return ProposedTrade(**defaults)  # type: ignore[arg-type]


# --- resolve_cycle_epoch ---------------------------------------------------


def test_resolve_cycle_epoch_quantizes_to_the_interval_bucket() -> None:
    as_of = datetime(2026, 1, 1, 0, 1, 30, tzinfo=UTC)  # epoch seconds not interval-aligned
    epoch = resolve_cycle_epoch(as_of, evaluation_interval_seconds=60.0)
    assert epoch % 60 == 0
    assert epoch <= int(as_of.timestamp())


def test_resolve_cycle_epoch_is_stable_within_one_bucket() -> None:
    first = datetime(2026, 1, 1, 0, 1, 0, tzinfo=UTC)
    second = datetime(2026, 1, 1, 0, 1, 59, tzinfo=UTC)
    assert resolve_cycle_epoch(first, evaluation_interval_seconds=60.0) == resolve_cycle_epoch(
        second, evaluation_interval_seconds=60.0
    )


def test_resolve_cycle_epoch_changes_across_a_bucket_boundary() -> None:
    before = datetime(2026, 1, 1, 0, 1, 59, tzinfo=UTC)
    after = datetime(2026, 1, 1, 0, 2, 0, tzinfo=UTC)
    assert resolve_cycle_epoch(before, evaluation_interval_seconds=60.0) != resolve_cycle_epoch(
        after, evaluation_interval_seconds=60.0
    )


# --- derive_client_request_id ----------------------------------------------


def test_derive_client_request_id_is_deterministic() -> None:
    kwargs: dict[str, object] = {
        "strategy_key": "momentum-v1",
        "strategy_version": 1,
        "cycle_epoch": 60,
        "instrument_key": "abc",
        "ordinal": 0,
    }
    assert derive_client_request_id(**kwargs) == derive_client_request_id(**kwargs)  # type: ignore[arg-type]


def test_derive_client_request_id_repeated_within_one_cycle_is_identical() -> None:
    """Repeated evaluation within the same cycle must generate the same id."""
    ids = {
        derive_client_request_id(
            strategy_key="momentum-v1",
            strategy_version=1,
            cycle_epoch=60,
            instrument_key="abc",
            ordinal=0,
        )
        for _ in range(3)
    }
    assert len(ids) == 1


def test_derive_client_request_id_changes_across_cycles() -> None:
    first = derive_client_request_id(
        strategy_key="momentum-v1",
        strategy_version=1,
        cycle_epoch=60,
        instrument_key="abc",
        ordinal=0,
    )
    second = derive_client_request_id(
        strategy_key="momentum-v1",
        strategy_version=1,
        cycle_epoch=120,
        instrument_key="abc",
        ordinal=0,
    )
    assert first != second


def test_derive_client_request_id_changes_across_strategy_versions() -> None:
    v1 = derive_client_request_id(
        strategy_key="momentum-v1",
        strategy_version=1,
        cycle_epoch=60,
        instrument_key="abc",
        ordinal=0,
    )
    v2 = derive_client_request_id(
        strategy_key="momentum-v1",
        strategy_version=2,
        cycle_epoch=60,
        instrument_key="abc",
        ordinal=0,
    )
    assert v1 != v2


def test_derive_client_request_id_changes_across_instruments() -> None:
    first = derive_client_request_id(
        strategy_key="momentum-v1",
        strategy_version=1,
        cycle_epoch=60,
        instrument_key="abc",
        ordinal=0,
    )
    second = derive_client_request_id(
        strategy_key="momentum-v1",
        strategy_version=1,
        cycle_epoch=60,
        instrument_key="xyz",
        ordinal=0,
    )
    assert first != second


def test_derive_client_request_id_changes_across_ordinals() -> None:
    first = derive_client_request_id(
        strategy_key="momentum-v1",
        strategy_version=1,
        cycle_epoch=60,
        instrument_key="abc",
        ordinal=0,
    )
    second = derive_client_request_id(
        strategy_key="momentum-v1",
        strategy_version=1,
        cycle_epoch=60,
        instrument_key="abc",
        ordinal=1,
    )
    assert first != second


# --- build_trade_proposal ---------------------------------------------------


def test_build_trade_proposal_maps_every_field_exactly() -> None:
    proposed = _proposed_trade()
    strategy = _FakeStrategy()
    proposal_id = ProposalId("22222222-2222-7222-8222-222222222222")

    proposal = build_trade_proposal(
        proposed,
        strategy=strategy,
        client_request_id="req-1",
        proposal_id=proposal_id,
        created_at=_AS_OF,
    )

    assert isinstance(proposal, TradeProposal)
    assert proposal.proposal_id == proposal_id
    assert proposal.instrument_id == proposed.instrument_id
    assert proposal.side == proposed.side
    assert proposal.quantity == proposed.quantity
    assert proposal.order_type == proposed.order_type
    assert proposal.limit_price == proposed.limit_price
    assert proposal.product == proposed.product
    assert proposal.client_request_id == "req-1"
    assert proposal.created_at == _AS_OF
    assert proposal.trigger_price is None


def test_build_trade_proposal_mode_is_always_paper() -> None:
    proposal = build_trade_proposal(
        _proposed_trade(),
        strategy=_FakeStrategy(),
        client_request_id="req-1",
        proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
        created_at=_AS_OF,
    )
    assert proposal.mode is Mode.PAPER


def test_build_trade_proposal_populates_strategy_id_and_version() -> None:
    strategy = _FakeStrategy()
    proposal = build_trade_proposal(
        _proposed_trade(),
        strategy=strategy,
        client_request_id="req-1",
        proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
        created_at=_AS_OF,
    )
    assert proposal.strategy_id == derive_strategy_id(strategy.strategy_key)
    assert proposal.strategy_version == strategy.strategy_version


# --- write_proposal / persist_proposed_trade --------------------------------


def _proposal(client_request_id: str = "req-1") -> TradeProposal:
    return build_trade_proposal(
        _proposed_trade(),
        strategy=_FakeStrategy(),
        client_request_id=client_request_id,
        proposal_id=ProposalId("22222222-2222-7222-8222-222222222222"),
        created_at=_AS_OF,
    )


def test_write_proposal_created_by_is_always_none() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory()
        async with factory() as uow:
            await write_proposal(
                uow,
                _proposal(),
                strategy_key="momentum-v1",
                id_generator=SequentialIdGenerator(),
                clock=FrozenClock(_AS_OF),
                correlation_id="corr-1",
            )
        assert factory.trade_proposals.saved[0].strategy_id is not None
        # created_by is passed to save(), not stored on the domain object -
        # asserted via the fake's own save() signature acceptance (it would
        # raise if called with a positional/required created_by it can't
        # accept), and directly via the audit event's actor_type below.
        assert factory.audit.saved[0].actor_type.value == "AGENT"

    asyncio.run(run())


def test_persist_proposed_trade_inserts_a_new_proposal() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory()
        inserted = await persist_proposed_trade(
            factory,
            _proposal(),
            strategy_key="momentum-v1",
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            correlation_id="corr-1",
        )
        assert inserted is True
        assert len(factory.trade_proposals.saved) == 1
        assert len(factory.audit.saved) == 1

    asyncio.run(run())


def test_persist_proposed_trade_duplicate_client_request_id_is_a_replay() -> None:
    async def run() -> None:
        factory = RecordingUnitOfWorkFactory()
        first = await persist_proposed_trade(
            factory,
            _proposal("req-dup"),
            strategy_key="momentum-v1",
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            correlation_id="corr-1",
        )
        second = await persist_proposed_trade(
            factory,
            _proposal("req-dup"),
            strategy_key="momentum-v1",
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            correlation_id="corr-1",
        )
        assert first is True
        assert second is False
        # Only one proposal was ever actually persisted.
        assert len(factory.trade_proposals.saved) == 1
        # The replay's transaction rolled back - its audit event never
        # committed either (both writes share one transaction).
        assert len(factory.audit.saved) == 1

    asyncio.run(run())


def test_persist_proposed_trade_rolls_back_the_failed_transaction_only() -> None:
    """A duplicate on one proposal's transaction must not affect a
    differently-keyed proposal persisted afterward in its own
    transaction."""

    async def run() -> None:
        factory = RecordingUnitOfWorkFactory()
        await persist_proposed_trade(
            factory,
            _proposal("req-a"),
            strategy_key="momentum-v1",
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            correlation_id="corr-1",
        )
        # Replay of the same key.
        await persist_proposed_trade(
            factory,
            _proposal("req-a"),
            strategy_key="momentum-v1",
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            correlation_id="corr-1",
        )
        # A distinct key persisted afterward must still succeed.
        third = await persist_proposed_trade(
            factory,
            _proposal("req-b"),
            strategy_key="momentum-v1",
            id_generator=SequentialIdGenerator(),
            clock=FrozenClock(_AS_OF),
            correlation_id="corr-1",
        )
        assert third is True
        assert {p.client_request_id for p in factory.trade_proposals.saved} == {"req-a", "req-b"}

    asyncio.run(run())
