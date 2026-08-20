# ADR-015: Strategy Actor Identity and Proposal Attribution

## Status
Accepted — Phase 1, Strategy Framework Milestone 2A (schema change only;
no `atp_strategy` service, role, grant, or runtime).

## Context
ADR-014 named `paper.trade_proposals.created_by` — `NOT NULL` with an FK
to `core.users.user_id` — as the concrete blocker preventing a strategy
from ever submitting a proposal, and deferred resolving it. This ADR
resolves it.

**The repository has already faced this exact dilemma once, and already
decided it.** Migration 0001's own module docstring
(`0001_core_audit_paper_schema.py:15-23`) records the Step 6
reconciliation:

> "docs/schemas/risk_config.md calls for 'one migration-seeded PAPER config
> row' with `created_by` pointing at an administrator, while
> docs/schemas/user.md is equally explicit that 'No default/seeded user in
> any migration' exists in Phase 1. Resolved by making
> `core.risk_config.created_by` nullable … mirroring
> `core.kill_switch_state.updated_by`'s existing NULL-for-system
> convention rather than fabricating a `core.users` row. No `core.users`
> row is created by this or any other migration."

Three columns already follow that convention today:
`core.risk_config.created_by` (nullable), `core.kill_switch_state.updated_by`
(nullable, with `CHECK (updated_by IS NULL OR reason IS NOT NULL)`), and
`core.kill_switch_history.changed_by` (nullable). A fourth precedent,
`core.job_queue.locked_by`, identifies a non-human actor with a plain
`Text` column carrying no FK at all. `audit.audit_events.actor_type`'s
CHECK already admits `'AGENT'` and `'SYSTEM'` alongside `'USER'`/`'BROKER'`,
and `actor_id` is a free-text nullable column — the audit ledger has been
able to record a non-human actor since Phase 1 Step 4 and has simply never
had one to record.

## Problem statement
How does a strategy-authored `TradeProposal` get written to
`paper.trade_proposals` when `created_by` is `NOT NULL` with an FK to
`core.users`, without creating a `core.users` row for a principal that
must never authenticate?

## Decision

### 1. `created_by` becomes nullable

```sql
ALTER TABLE paper.trade_proposals ALTER COLUMN created_by DROP NOT NULL;
```

The FK to `core.users.user_id` is unchanged — a non-NULL `created_by` must
still reference a real user row. Only the `NOT NULL` constraint is
dropped.

### 2. `strategy_id` is the strategy-attribution field — no new column, no new table

`strategy_id` already exists on `paper.trade_proposals` (Phase 1 Step 9/10)
and was, until this migration, wholly unconstrained. A strategy-authored
proposal carries `created_by = NULL` and `strategy_id = <the strategy's
derived UUID>` (ADR-014 §4's `derive_strategy_id`). No new column, table,
or migration-seeded row is introduced to represent "which strategy."

### 3. The attribution invariant — a new CHECK constraint

```sql
ALTER TABLE paper.trade_proposals
  ADD CONSTRAINT ck_trade_proposals_proposal_has_an_author
  CHECK (created_by IS NOT NULL OR strategy_id IS NOT NULL);
```

**This makes the table strictly stronger, not weaker.** Before this
migration, nothing prevented a row with both `created_by` and
`strategy_id` NULL — `strategy_id` was optional and unconstrained. After
it, every row is attributable to exactly a human (`created_by` set) or a
strategy (`strategy_id` set), and the database — not application
discipline — enforces that a proposal is never anonymous.

### 4. Why a real `core.users` row per strategy is rejected

Considered and rejected, for reasons that go beyond preference:

- **`docs/schemas/user.md`** states plainly: "No default/seeded user in
  any migration." A strategy identity seeded by a migration violates this
  directly.
- **`core.users.role`'s CHECK constraint** admits only five human role
  values (`viewer`, `researcher`, `paper_trader`, `live_trader`,
  `administrator`) — there is no role value that means "non-human service
  identity," and inventing one would require touching
  `atp_api.security.rbac`'s permission map, which is unrelated to this
  milestone's scope.
- **`password_hash` is `NOT NULL`** — a strategy row would need a
  fabricated hash for a principal that must never log in, with
  `is_active`/`must_change_password` describing a login flow that
  structurally cannot apply to it.
- **It would permanently disable admin bootstrap.** `bootstrap_admin`
  (`atp_api.bootstrap`) refuses whenever `core.users` has *any* row —
  that is its entire one-time-use enforcement mechanism. A
  migration-seeded strategy user would make every future deployment's
  first-administrator bootstrap impossible, breaking a capability
  verified and checkpointed two milestones ago.
- **No Phase 1 role but `atp_api` holds any privilege on `core.users`** —
  migration 0003 revokes all access from `atp_paper_exec` and
  `atp_worker`. A future `atp_strategy` role reading `core.users` to
  resolve its own identity would be a new, otherwise-unnecessary widening
  of who can read that table, including every other user's password
  hash.

### 5. Audit attribution expectations

Reuse the existing vocabulary; no new `ACTION_*` constant. A
strategy-authored proposal's `AuditEvent` sets `actor_type=ActorType.AGENT`
(already in the `valid_actor_type` CHECK, currently dormant),
`actor_id=f"strategy/{strategy_key}"`, and populates `strategy_id`/
`strategy_version` for the first time — those fields have existed on
`AuditEvent` since Phase 1 Step 9/10 with no writer. This ADR does not
implement that write path (no `atp_strategy` service exists yet); it
records the expectation for the milestone that does.

## Alternatives considered
- **A real `core.users` service-identity row per strategy** — rejected;
  §4 above.
- **A separate strategy-actor table with `created_by` re-targeted to a
  union/polymorphic reference** — rejected as unnecessary complexity: it
  would require re-targeting a live, populated FK and inventing a new
  table lifecycle to own, for a problem the existing nullable-column
  convention already solves with zero new schema surface.
- **Leave `created_by` `NOT NULL` and give every strategy a synthetic but
  non-authenticatable "system" `core.users` row shared across all
  strategies** — rejected: collapses per-strategy attribution into one
  shared identity, defeating the auditability this milestone exists to
  provide, and still violates the "no seeded user" rule.

## Security / least-privilege implications
No new role, grant, or credential is introduced by this migration. No
process gains any new access to `core.users`. The `proposal_has_an_author`
CHECK is a pure data-integrity control, not a privilege change.

## Data-access implications
None beyond the DDL itself. `atp_api` continues to always supply a real
`principal.user_id` as `created_by`; nothing about its behavior changes.
The eventual `atp_strategy` role's grants (INSERT-only on
`paper.trade_proposals`, no `core.users` access) are specified in a later
milestone's ADR, not here.

## Transaction implications
None — this is schema DDL applied once via Alembic, not a data migration.
Every existing row already satisfies the new CHECK (every existing
`trade_proposals` row has `created_by IS NOT NULL`), so no backfill is
required.

## Testing implications
- Unit: `atp_persistence.mappers.trade_proposal_to_row` and
  `SqlAlchemyTradeProposalRepository.save` accept `created_by: str | None`;
  existing human-authored callers are unaffected structurally (widening
  `str` → `str | None` accepts every existing caller unchanged).
- Docker integration: the CHECK accepts `(created_by set, strategy_id
  NULL)`, accepts `(created_by NULL, strategy_id set)`, and rejects
  `(both NULL)`; `alembic upgrade head` / `downgrade` both succeed against
  real PostgreSQL and are round-tripped by test.

## Explicit non-scope
No `atp_strategy` service, package, role, or grant. No scheduling or
runtime. No reference strategy. No market-data integration. No change to
`atp_worker`, `atp_exec_paper`, `atp_api` behavior, kill switches, rate
limiting, or the scheduler. No new `ACTION_*` audit constant. No `core.users`
row created by any migration.

## Deferred decisions
- The `atp_strategy` role's exact grants (Milestone 2B).
- The runner that actually writes a strategy-authored proposal, and its
  transaction/idempotency model (Milestone 2C).
- Whether a future non-strategy, non-human actor (e.g. a future
  `atp_worker`-initiated action) should also use `created_by = NULL`, or
  whether that remains specific to strategies — not needed by anything in
  Phase 1.

## Consequences
`paper.trade_proposals` becomes attributable-by-construction rather than
attributable-by-convention: the database itself now guarantees every row
traces to exactly a human or a strategy. This unblocks Milestone 2B/2C
without requiring any further schema change to `paper.trade_proposals`
for actor identity. The nullable-`created_by` / dormant-`AGENT`-actor-type
pattern this ADR completes was already half-built into the schema three
migrations ago; this ADR is the second of two required steps (the first,
`core.risk_config.created_by`, shipped in migration 0001) to extend it
to proposals.
