"""`atp_strategy.uow.StrategyUnitOfWork` - exposes exactly the four
repositories the `atp_strategy` database role holds grants for (ADR-014,
ADR-015), and no others. No database connection is used - this module
proves the class's attribute surface, not its runtime I/O behavior."""

from __future__ import annotations

from atp_strategy.uow import StrategyUnitOfWork, strategy_unit_of_work


def test_strategy_unit_of_work_exposes_exactly_the_approved_repositories() -> None:
    exposed = {
        name
        for name in vars(StrategyUnitOfWork(session=None))  # type: ignore[arg-type]
        if not name.startswith("_")
    }
    assert exposed == {"instruments", "kill_switches", "trade_proposals", "audit"}


def test_strategy_unit_of_work_does_not_subclass_the_shared_unit_of_work() -> None:
    """ADR-014 §A / ADR-015: a dedicated UoW, not a reuse of
    atp_persistence.db.UnitOfWork - that class also carries users/sessions
    repositories atp_strategy holds no grant on."""
    from atp_persistence.db import UnitOfWork

    assert not issubclass(StrategyUnitOfWork, UnitOfWork)


def test_strategy_unit_of_work_context_manager_is_exported() -> None:
    assert callable(strategy_unit_of_work)
