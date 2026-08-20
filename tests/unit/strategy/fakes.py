"""In-memory fakes for `atp_strategy`'s transaction and repository
dependencies.

`atp_strategy.runner`'s whole job is *transaction choreography*
(Milestone 2C §4's four boundaries) - `RecordingUnitOfWorkFactory` mirrors
`tests/unit/worker/fakes.py`'s precedent exactly: it appends every
open/commit/rollback to one shared `events` list and shares one set of
repository fakes across every transaction it opens, the way one real
database is shared across successive short transactions.

Not a test file itself - no `test_*` function lives here, so pytest does
not collect it (mirrors `tests/unit/exec_paper/fakes.py`'s precedent).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from types import TracebackType

from sqlalchemy.exc import IntegrityError

from atp_domain.audit import AuditEvent
from atp_domain.proposals import TradeProposal
from atp_domain.strategy import ProposedTrade, StrategyContext


@dataclass(frozen=True, slots=True)
class FakeInstrumentRow:
    """Duck-typed to the subset of `atp_persistence.models.core
    .InstrumentRow` `atp_strategy.context._project_instrument` reads."""

    instrument_id: str
    symbol: str
    lot_size: int
    tick_size: Decimal


class FakeInstrumentRepository:
    """Duck-typed to `SqlAlchemyInstrumentRepository`'s `list_active`."""

    def __init__(self, rows: list[FakeInstrumentRow] | None = None) -> None:
        self.rows = list(rows or [])
        self.list_active_calls = 0

    async def list_active(self) -> Sequence[FakeInstrumentRow]:
        self.list_active_calls += 1
        return list(self.rows)


class FakeKillSwitchStateRepository:
    """Duck-typed to `SqlAlchemyKillSwitchStateRepository.list_all`."""

    def __init__(
        self, snapshots: list[object] | None = None, *, raise_on_read: bool = False
    ) -> None:
        self._snapshots = snapshots or []
        self._raise_on_read = raise_on_read
        self.list_all_calls = 0

    async def list_all(self) -> list[object]:
        self.list_all_calls += 1
        if self._raise_on_read:
            raise ConnectionError("simulated kill-switch read failure")
        return self._snapshots


class FakeTradeProposalRepository:
    """Duck-typed to `SqlAlchemyTradeProposalRepository.save` - simulates
    `UNIQUE(client_request_id)` by raising a real `IntegrityError` on a
    collision, so `persist_proposed_trade`'s replay detection is
    exercised genuinely, not merely asserted about (mirrors
    `tests/unit/exec_paper/fakes.py`'s precedent for the same
    constraint)."""

    def __init__(self) -> None:
        self.saved: list[TradeProposal] = []
        self._client_request_ids: set[str] = set()

    async def save(self, proposal: TradeProposal, *, created_by: str | None) -> None:
        if proposal.client_request_id in self._client_request_ids:
            raise IntegrityError(
                "INSERT INTO paper.trade_proposals ...",
                {},
                Exception("duplicate key value violates unique constraint"),
            )
        self._client_request_ids.add(proposal.client_request_id)
        self.saved.append(proposal)


class FakeAuditEventWriter:
    """Duck-typed to `SqlAlchemyAuditEventWriter`."""

    def __init__(self) -> None:
        self.saved: list[AuditEvent] = []

    async def save(self, event: AuditEvent) -> None:
        self.saved.append(event)


@dataclass
class FakeStrategyUnitOfWork:
    """Exposes the same four attributes a real `StrategyUnitOfWork`
    does."""

    instruments: FakeInstrumentRepository
    kill_switches: FakeKillSwitchStateRepository
    trade_proposals: FakeTradeProposalRepository
    audit: FakeAuditEventWriter


class _RecordingTransaction:
    """One `async with uow_factory()` block. Records its own lifecycle so
    a test can assert exactly where a transaction opened and closed
    relative to a strategy's `evaluate()` call."""

    def __init__(self, factory: RecordingUnitOfWorkFactory) -> None:
        self._factory = factory

    async def __aenter__(self) -> FakeStrategyUnitOfWork:
        self._factory.events.append("tx_open")
        self._factory.open_transactions += 1
        return FakeStrategyUnitOfWork(
            instruments=self._factory.instruments,
            kill_switches=self._factory.kill_switches,
            trade_proposals=self._factory.trade_proposals,
            audit=self._factory.audit,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # Mirrors `strategy_unit_of_work`'s real contract: roll back on
        # any exception (and let it propagate), commit on a clean exit.
        self._factory.events.append("tx_rollback" if exc_type is not None else "tx_commit")
        self._factory.events.append("tx_close")
        self._factory.open_transactions -= 1
        return False


@dataclass
class RecordingUnitOfWorkFactory:
    """A `UnitOfWorkFactory` (zero-argument, returns an async context
    manager) that records transaction lifecycle events and shares one set
    of repository fakes across every transaction it opens."""

    instruments: FakeInstrumentRepository = field(default_factory=FakeInstrumentRepository)
    kill_switches: FakeKillSwitchStateRepository = field(
        default_factory=FakeKillSwitchStateRepository
    )
    trade_proposals: FakeTradeProposalRepository = field(
        default_factory=FakeTradeProposalRepository
    )
    audit: FakeAuditEventWriter = field(default_factory=FakeAuditEventWriter)
    events: list[str] = field(default_factory=list)
    open_transactions: int = 0

    def __call__(self) -> _RecordingTransaction:
        return _RecordingTransaction(self)

    @property
    def transactions_opened(self) -> int:
        return self.events.count("tx_open")


class RecordingStrategy:
    """A `Strategy` that records every `StrategyContext` it was called
    with, and - critically - how many transactions were open (per the
    `RecordingUnitOfWorkFactory` it is told about) at the moment
    `evaluate()` ran, which is what proves Milestone 2C's "evaluate() runs
    outside a transaction" invariant directly rather than by inference."""

    def __init__(
        self,
        *,
        strategy_key: str = "fake-strategy",
        strategy_version: int = 1,
        proposed_trades_by_call: list[Sequence[ProposedTrade]] | None = None,
        raises: BaseException | None = None,
        factory: RecordingUnitOfWorkFactory | None = None,
    ) -> None:
        self._strategy_key = strategy_key
        self._strategy_version = strategy_version
        self._proposed_trades_by_call = list(proposed_trades_by_call or [])
        self._raises = raises
        self.factory = factory
        self.calls: list[StrategyContext] = []
        self.open_transactions_when_called: list[int] = []

    @property
    def strategy_key(self) -> str:
        return self._strategy_key

    @property
    def strategy_version(self) -> int:
        return self._strategy_version

    def evaluate(self, context: StrategyContext) -> Sequence[ProposedTrade]:
        self.calls.append(context)
        if self.factory is not None:
            self.open_transactions_when_called.append(self.factory.open_transactions)
        if self._raises is not None:
            raise self._raises
        if self._proposed_trades_by_call:
            return self._proposed_trades_by_call.pop(0)
        return []
