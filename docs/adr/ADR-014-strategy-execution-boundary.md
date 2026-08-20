# ADR-014: Strategy Execution Boundary — Location, Identity, and the Proposal Path

## Status
Accepted — Phase 1, Strategy Framework Milestone 1 (design and minimal
domain scaffolding only; no runtime).

## Context
`paper.trade_proposals`/`audit.audit_events` have carried unused
`strategy_id`/`strategy_version` columns since Phase 1 Step 9/10
(`persistence/src/atp_persistence/models/paper.py`,
`persistence/src/atp_persistence/models/audit.py`), and
`atp_domain.proposals.TradeProposal`/`atp_domain.audit.AuditEvent` have
carried the matching typed fields since the same steps
(`domain/src/atp_domain/proposals.py:45-46`,
`domain/src/atp_domain/audit.py:54-55`) — nothing has ever populated them.
`atp_domain.risk.engine.RiskDecision.evaluate()` is origin-agnostic: it
has no knowledge of whether a `TradeProposal` came from a human via
`POST /api/v1/paper/proposals` (ADR-012) or from any other source.
`SwitchScope.STRATEGY` has existed as a live, administratively-mutable
kill-switch scope since ADR-007 and this session's kill-switch admin
API milestone, with no consumer. Together these are unused plumbing
already built for exactly this capability.

ADR-013 states, as its own literal invariant: "`atp_worker` is never an
execution invocation path... a future phase that wants worker-driven
execution must reopen this ADR rather than add an import." This ADR is
that reopening — deciding, before any code is written, where strategy
evaluation runs and how its output reaches the existing
`TradeProposal → RiskDecision → ApprovedOrderIntent → PAPER execution`
pipeline without weakening any link in it.

Three facts, confirmed by direct inspection, materially constrain the
design below:

1. **`paper.trade_proposals.created_by` is `NOT NULL` with a real
   foreign key to `core.users.user_id`**
   (`persistence/src/atp_persistence/models/paper.py:65`). A strategy
   cannot submit a proposal today without a real `core.users` row
   representing it as an actor — this is a genuine blocker for the next
   milestone (proposal submission), not solved here (§ Deferred
   decisions).
2. **`trade_proposals.strategy_id`/`audit_events.strategy_id` are
   genuine Postgres `uuid` columns**
   (`uuid_column()` → `UUID(as_uuid=False)`,
   `persistence/src/atp_persistence/models/paper.py:60`,
   `persistence/src/atp_persistence/models/audit.py:58`) — not free
   text. A human-readable strategy name cannot be stored there directly.
3. **`atp_domain` may not import `atp_persistence`.** `pyproject.toml`'s
   "Domain kernel stays framework-free" import-linter contract forbids
   it explicitly, and the "Layered architecture" contract places
   `atp_domain` innermost, below `atp_persistence`. Any domain-owned
   type a strategy consumes must therefore be domain-owned, not reused
   directly from a persistence-layer repository return type.

## Problem statement
Where does strategy-evaluation code run, under which database role, with
what identity scheme, and how does its output enter the existing
`TradeProposal → RiskDecision → ApprovedOrderIntent → PAPER execution`
path without creating a second, weaker path?

## Decision

### 1. Execution location
A future, dedicated `atp_strategy` service/role — mirroring the existing
`atp_api`/`atp_paper_exec`/`atp_worker` three-way separation with a
fourth, equally narrow member. **Not** built in this milestone; decided
here so the next milestone has an unambiguous target.

### 2. Proposal creation boundary
A future strategy submits `ProposedTrade` output that becomes a
`TradeProposal` through the existing intake path (ADR-012), with **no
per-proposal human confirmation step**. The existing
`SwitchScope.STRATEGY:{strategy_key}` kill switch is the human-in-the-
loop control, applied at the *strategy* level: `SwitchState.UNAVAILABLE`
(no row) already resolves as blocking, identically to `ENGAGED`
(`atp_domain.killswitch.resolve_switch_state`/`is_blocking`), so a newly
registered strategy is blocked by default until an administrator
explicitly `DISENGAGE`s it. The pipeline remains exactly:

```
Strategy.evaluate() → ProposedTrade → (future) submit_proposal
  → TradeProposal → RiskDecision.evaluate() → mint_intent_for_decision()
  → ApprovedOrderIntent → PAPER execution gateway
```

No new path bypasses the risk engine or the minting-capability boundary.
A strategy never imports `atp_domain.intents` or
`atp_domain.risk.engine`.

### 3. Strategy Protocol
`domain/src/atp_domain/strategy.py` defines `StrategyContext`,
`ProposedTrade`, and the `Strategy` Protocol. `evaluate()` is
synchronous and pure — no I/O, no wall-clock reads, no database access,
no direct execution/risk calls. Same `StrategyContext` in ⇒ same
`Sequence[ProposedTrade]` out, always. A future runner resolves all
inputs (market data, instrument lookup, `as_of`) before constructing
the context; `evaluate()`'s signature never changes when a fixture data
source is swapped for a real one.

**Amendment (Milestone 2C).** `ProposedTrade` does **not** carry a
`client_request_id` field. Idempotency is a platform guarantee, not a
strategy one: `atp_strategy` holds no `SELECT` grant on
`paper.trade_proposals` (ADR-015) to detect its own duplicates, so a
strategy-supplied key could never be safely deduplicated by the caller.
`atp_strategy.proposals.derive_client_request_id` derives it
deterministically instead, from `(strategy_key, strategy_version,
cycle_epoch, instrument_id, ordinal)`, after `evaluate()` returns — see
Milestone 2C's own design notes for the exact quantization.

`StrategyContext.instruments` is typed against a **domain-owned**
`InstrumentSnapshot` (identity, symbol, lot size, tick size) rather than
`atp_persistence.repositories.instruments.InstrumentSnapshot` — fact 3
above makes reusing the persistence-layer type structurally impossible,
not merely undesirable. A future runner projects the persistence row
into this domain shape before constructing a context, the same way
`atp_domain.ports.marketdata.Quote` is already independent of whatever
adapter produces it.

### 4. Strategy identity
Two identifiers:
- `strategy_key: str` — the real identity: stable, human-readable,
  developer-assigned (e.g. `"momentum-v1"`). Used for registry lookup,
  the kill-switch qualifier, and logs.
- `strategy_id: StrategyId` — a genuine UUID string, required only
  because of fact 2 above. **Derived, never stored**:
  `uuid.uuid5(_STRATEGY_NAMESPACE, strategy_key)`
  (`atp_domain.strategy.derive_strategy_id`), so the same `strategy_key`
  always produces the same `strategy_id` on every process restart, with
  no registry table and no migration. `_STRATEGY_NAMESPACE` is a fixed,
  hardcoded namespace UUID constant.

### 5. Strategy Registry
A static, code-defined `StrategyRegistry` (`register`, `get`, `all`),
mirroring `atp_domain.risk.registry.RuleRegistry`'s existing shape more
closely than `atp_worker.registry.HANDLER_REGISTRY` (the same layer,
the same "explicit registration, fail-fast on collision" pattern).
Duplicate `strategy_key` registration raises `DuplicateStrategyError`
immediately. Built once at process start, immutable afterward. **Not a
generic plugin/discovery ecosystem** — no dynamic loading, no
entry-points, no external strategy packages, no database-backed
strategy table.

### 6. Versioning / audit
`strategy_version` is a positive integer bumped only by a strategy's own
author, never auto-incremented by the registry or a runner. A future
runner captures `strategy_id`/`strategy_version` from the evaluating
`Strategy` instance at the moment it produces a proposal and stores them
immutably on that `trade_proposals`/`audit_events` row — both fields
already exist on `TradeProposal`/`AuditEvent`. The registry holds exactly
one `Strategy` instance per `strategy_key` at a time; there is no
side-by-side multi-version execution in Phase 1.

### 7. Future kill-switch consultation (documented, not wired)
A future strategy runner will mirror
`atp_exec_paper.kill_switch_adapter` exactly: load switch state rows,
build `dict[SwitchId, SwitchState]` via the same pure-function shape as
`build_kill_switch_states`, resolve `SwitchId(scope=SwitchScope.STRATEGY,
qualifier=strategy.strategy_key)` through the existing
`resolve_switch_state`/`is_blocking` functions, and fail closed (skip
every strategy this cycle) on any read error — the same deliberate
broad-exception catch `load_kill_switch_states` already uses. Nothing
here is wired in this milestone.

### 8. Safety boundary (documented, not enforced by a new contract yet)
A future `atp_strategy` package and every `Strategy` implementation
registered into it must never import or reach: `atp_exec_paper` (any
submodule), `atp_api`, `atp_domain.intents`, `atp_domain.risk.engine`,
`atp_persistence.models.paper`, any broker/Kite/MCP module, any LLM
decision-making module, or `httpx`/`requests`/`aiohttp`. No new
import-linter contract is added this milestone — `atp_domain.strategy`
lives inside `atp_domain`, already covered by the existing "framework-free
domain kernel" and "no egress" contracts with zero edits. A contract
naming `atp_strategy` specifically becomes necessary only once that
package exists.

### 9. Milestone-2 blocker (not solved here)
Strategy proposal submission is **blocked** until fact 1 above
(`created_by` NOT NULL FK to `core.users`) is resolved. This ADR states
it as a hard prerequisite for the next execution milestone and does not
solve it, modify the schema, or pick between its two live options
(§ Deferred decisions).

## Alternatives considered
- **Widen `atp_worker`** (grant it `INSERT` on `paper.trade_proposals`):
  rejected — directly contradicts ADR-013's own stated invariant, mixes
  job-processing transactions with business-proposal-creation
  transactions, and erases the audit clarity of "which role wrote this
  row."
- **Run strategy evaluation inside `atp_api`**: rejected — `atp_api` is
  the internet-facing, session/CSRF/RBAC-gated surface; embedding
  autonomous scheduled computation in it blurs its threat model, and it
  has no existing scheduling primitive (that is deliberately what
  `atp_worker` was built to be, kept separate).
- **Per-proposal human confirmation**: rejected — duplicates the risk
  engine's gate at the wrong layer and defeats the purpose of a
  "strategy" (autonomous, scheduled generation), turning it into a
  suggestion list, a materially different product.
- **Store `strategy_key` directly in the UUID column**: rejected — not a
  valid UUID; would break the column type.
- **A database-backed strategy registry**: rejected as premature — no
  requirement today needs dynamic registration, and the task's own
  constraint is not to build a generic plugin ecosystem unless
  necessary.
- **Reuse `atp_persistence.repositories.instruments.InstrumentSnapshot`
  in `StrategyContext`**: rejected — structurally forbidden by the
  domain-independence import-linter contract (fact 3); a domain-owned
  equivalent is used instead.

## Security / least-privilege implications
The future `atp_strategy` role will be narrower than any existing role:
read-only on reference data (`core.instruments`, `core.kill_switch_state`)
plus insert-only on `paper.trade_proposals`, once built. `atp_worker`'s
existing boundary is left completely intact — ADR-013's security argument
is undiminished, not merely unchanged. No credentials, grants, or roles
are created by this milestone.

## Data-access implications
`atp_strategy` will eventually need `SELECT` on `core.instruments` and
`core.kill_switch_state`, and `INSERT` on `paper.trade_proposals` — no
grant is made this milestone. The `created_by` FK problem (fact 1) is
explicitly unresolved, with two live options for the next milestone: (a)
a real `core.users` row acting as a service identity for each strategy,
or (b) a schema change loosening the FK. This milestone makes no
migration and no schema change.

## Transaction implications
A future `StrategyUnitOfWork` will mirror
`atp_exec_paper.uow.PaperExecutionUnitOfWork` exactly — its own
`AsyncSession`, its own commit/rollback wiring duplicated rather than
shared with any other service's `UnitOfWork`, exposing only the
repositories the new role actually holds grants for. Not built this
milestone.

## Testing implications
This milestone adds direct unit tests for the domain scaffolding only
(registry registration/lookup/duplicate-rejection, `derive_strategy_id`
determinism, `Strategy` Protocol structural conformance,
`StrategyContext`/`ProposedTrade` immutability). The full future
mechanical contract — AST import-boundary scan, forbidden-parameter-name
scan, kill-switch-respected proof, real-PostgreSQL grant proof — mirrors
`tests/safety/test_no_execution_path_in_worker.py`'s existing structure
and is deferred to the milestone that creates the `atp_strategy` package
and role.

## Explicit non-scope
No new service. No new grants. No migrations. No scheduling. No running
strategies. No real market data. No backtesting. No frontend. No LLM
strategy generation. No broker/live execution. No import-linter contract
edit.

## Deferred decisions
- The `created_by`/`core.users` FK problem (fact 1) — how a strategy
  obtains a valid, real actor identity to satisfy the existing `NOT NULL`
  FK, ideally without a migration.
- Whether `evaluate()` ever needs a live, scoped `MarketDataPort` handle
  instead of a pre-fetched snapshot — not needed by any strategy that
  exists, because none exist yet.
- Side-by-side multi-version strategy execution (gradual rollout / A-B
  testing) — Phase 1 runs exactly one version per `strategy_key`.
- The exact scheduling mechanism for the future `atp_strategy` service
  (its own poll loop vs. triggered by `atp_worker`) — deferred to that
  milestone's own design pass.
- The new import-linter contract naming `atp_strategy` — deferred until
  that package exists.
- Whether historical `bars` belong in `StrategyContext` and in what
  shape — deferred to whenever `MarketDataPort.get_historical_bars`'s
  return type is finalized (already flagged Phase 2 in its own
  docstring).

## Consequences
`SwitchScope.STRATEGY` becomes load-bearing instead of dormant plumbing.
`atp_domain.strategy` becomes the shared contract both a future live
strategy runner and a future backtester can consume identically —
backtesting reuse is free precisely because `Strategy`/`ProposedTrade`/
`StrategyContext` live in the domain layer, decoupled from any execution
mechanism. `uuid5`-derived identity is this repository's first precedent
for deterministic, non-persisted UUID derivation. `atp_worker`'s
boundary (ADR-013) is reaffirmed, not weakened, by explicitly routing
strategy execution to a different future service instead.
