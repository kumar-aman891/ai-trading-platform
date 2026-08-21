"""Kill-switch state query and mutation support (Phase 1 Step 7 read
foundation; Phase 1 Step 14 mutation, ADR-007 "Kill-Switch Taxonomy and
Scope").

`KillSwitchStateSnapshot` is a persistence-level read projection, not a
domain type - it mirrors `core.kill_switch_state`'s stored columns exactly
(`engaged` is the raw stored boolean). It is deliberately *not* added to
`atp_domain.ports.storage` as a repository Protocol: interpreting a stored
row (three-state ENGAGED/DISENGAGED/UNAVAILABLE fail-closed policy) is
`atp_domain.killswitch`'s job, not this query's - this class only reads
what is currently stored, unchanged.

`apply_transition` was deferred at Step 7 to "Step 8 authentication/RBAC,
per the kill-switch API foundation scope note" - an unauthenticated caller
must never engage/disengage a switch, and Step 7 predates auth entirely.
Auth/RBAC (Step 8) has been complete since then; this is that deferred
mutation, gated by `atp_api.security.rbac.Permission.ENGAGE_KILL_SWITCH`/
`DISENGAGE_KILL_SWITCH` at the route layer, never here - this class has no
opinion on who is allowed to call it, the same way `SqlAlchemyJobQueueRepository`
executes SQL a caller's policy decision translates into without re-deciding
that policy itself.

Which scopes are administratively mutable at all (`GLOBAL_LIVE`/
`LIVE_ACCOUNT` are not, ADR-007) is also not this module's concern - that
check happens once, in `atp_domain.killswitch.MUTABLE_SWITCH_SCOPES`, and
again in `atp_api.services.kill_switches` before this repository is ever
reached. `apply_transition` will happily write a state row for any
`switch_id` string it is given; nothing calls it for `GLOBAL_LIVE`/
`LIVE_ACCOUNT` today, and the safety suite proves that mechanically at the
route layer rather than this module re-deriving the same fact.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atp_persistence.models.core import KillSwitchHistoryRow, KillSwitchStateRow


@dataclass(frozen=True, slots=True)
class KillSwitchStateSnapshot:
    switch_id: str
    engaged: bool
    updated_at: datetime
    updated_by: str | None
    reason: str | None


class SqlAlchemyKillSwitchStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> Sequence[KillSwitchStateSnapshot]:
        result = await self._session.execute(
            select(KillSwitchStateRow).order_by(KillSwitchStateRow.switch_id)
        )
        return [
            KillSwitchStateSnapshot(
                switch_id=row.switch_id,
                engaged=row.engaged,
                updated_at=row.updated_at,
                updated_by=row.updated_by,
                reason=row.reason,
            )
            for row in result.scalars().all()
        ]

    async def get_current(self, switch_id: str) -> KillSwitchStateSnapshot | None:
        """A plain (non-locking) single-row read. `None` means no row
        exists yet for `switch_id` - true today only for `STRATEGY:{id}`/
        `INSTRUMENT:{id}` before their first ever transition
        (`GLOBAL_LIVE`/`LIVE_ACCOUNT`/`PAPER`/`API_EXECUTION` are all
        migration-seeded, `0001_core_audit_paper_schema.py`).

        `atp_api.services.kill_switches` uses this to decide *before*
        minting an audit event whether a requested transition is a no-op
        (the requested `engaged` value already matches). That decision
        happens on this unlocked read, not inside `apply_transition`'s own
        locked one - a deliberate, narrow trade-off: two concurrent
        identical requests could both observe "a change is needed" and
        both proceed, producing one redundant but harmless history/audit
        entry, rather than holding a row lock open across two service-
        layer round trips (mint ID, build `AuditEvent`, save it) for a
        low-frequency, role-gated admin action. `apply_transition` still
        takes its own lock and its own read for the value it actually
        writes, so the *stored* state is never lost or corrupted by this
        - only `kill_switch_history.previous_engaged` could, in that rare
        race, read as `False->True` twice instead of once."""
        result = await self._session.execute(
            select(KillSwitchStateRow).where(KillSwitchStateRow.switch_id == switch_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return KillSwitchStateSnapshot(
            switch_id=row.switch_id,
            engaged=row.engaged,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
            reason=row.reason,
        )

    async def apply_transition(
        self,
        switch_id: str,
        *,
        new_engaged: bool,
        changed_by: str,
        reason: str,
        now: datetime,
        history_id: str,
        audit_event_id: str,
    ) -> KillSwitchStateSnapshot:
        """Writes `core.kill_switch_state` and appends
        `core.kill_switch_history` in the caller's already-open
        transaction (`UnitOfWork` - never a transaction of its own; ADR-010
        requires the audit event this row's `audit_event_id` names to
        commit atomically with it, and only the caller's `UnitOfWork`
        spans both).

        `FOR UPDATE` locks the row for this write specifically - not to
        implement the no-op check (`get_current` already made that
        decision), but because the write itself must not lose a
        concurrent update. `previous_engaged` is read from *this* lock,
        not trusted from an earlier `get_current` call, so the appended
        history row's `previous_engaged` reflects what was actually
        overwritten by this statement. A missing row (first-ever
        transition of a `STRATEGY:{id}`/`INSTRUMENT:{id}` switch) is
        inserted fresh, with `previous_engaged = False` recorded in the
        history row - documented interpretation, not an ADR-007 rule
        text can name: nothing was "previously engaged" for a switch that
        did not yet exist in storage, so the least surprising value for a
        NOT NULL boolean column is "false, then this transition happened."

        `core.kill_switch_history.audit_event_id` carries no
        database-level `FOREIGN KEY` (`docs/schemas/kill_switch_history.md`
        describes the linkage; `KillSwitchHistoryRow.audit_event_id` is a
        plain `uuid_column()`, verified against
        `persistence/src/atp_persistence/models/core.py` before writing
        this) - so insert ordering relative to the `audit.audit_events`
        row is a documentation contract the caller must honor, not one
        this database enforces, but nothing here depends on ordering
        either way since `audit_event_id` is minted by the caller before
        this call, not read back afterward."""
        result = await self._session.execute(
            select(KillSwitchStateRow)
            .where(KillSwitchStateRow.switch_id == switch_id)
            .with_for_update()
        )
        row = result.scalar_one_or_none()
        previous_engaged = False if row is None else row.engaged

        if row is None:
            row = KillSwitchStateRow(
                switch_id=switch_id,
                engaged=new_engaged,
                updated_at=now,
                updated_by=changed_by,
                reason=reason,
            )
            self._session.add(row)
            # Emit the parent INSERT *before* the history row is added below.
            # `atp_persistence.models` declares no `relationship()` anywhere
            # (deliberate house style: plain FK columns only), so SQLAlchemy's
            # unit of work has no mapper-level dependency by which to order
            # these two INSERTs and is free to emit the child first - which it
            # does, violating `fk_kill_switch_history_switch_id_kill_switch_state`
            # and failing the first-ever transition of every
            # `STRATEGY:{id}`/`INSTRUMENT:{id}` switch. The seeded switches take
            # the UPDATE branch below and never hit it, which is why only
            # `test_apply_transition_creates_a_strategy_switch_on_first_touch`
            # (against a real database) ever caught this - in-memory fakes
            # structurally cannot. A flush is not a commit: the caller's
            # transaction still spans both rows and the ADR-010 audit event.
            await self._session.flush()
        else:
            row.engaged = new_engaged
            row.updated_at = now
            row.updated_by = changed_by
            row.reason = reason

        self._session.add(
            KillSwitchHistoryRow(
                history_id=history_id,
                switch_id=switch_id,
                previous_engaged=previous_engaged,
                new_engaged=new_engaged,
                changed_at=now,
                changed_by=changed_by,
                reason=reason,
                audit_event_id=audit_event_id,
            )
        )
        await self._session.flush()

        return KillSwitchStateSnapshot(
            switch_id=switch_id,
            engaged=new_engaged,
            updated_at=now,
            updated_by=changed_by,
            reason=reason,
        )
