# ADR-005: PAPER/LIVE Isolation via Separate Schemas, Services, and Risk Config

## Status
Accepted — Phase 1.

## Context
CLAUDE.md rule #3 requires PAPER and LIVE execution paths to be "structurally
separated, not merely UI-labeled." The original schema sketch in
[docs/SCHEMAS.md](../SCHEMAS.md) modeled the distinction as a `mode` column
on `Order` and `Position` only — a label, not a structural boundary — and
omitted `mode` from `Signal`, `TradeProposal`, `RiskDecision`, and `Fill`
entirely.

## Decision
1. **Separate PostgreSQL schemas**: `paper.*` and `live.*`, plus a
   mode-agnostic `core.*` for reference data (instruments, users, risk
   config, kill switches) and an immutable `audit.*`.
2. **Separate execution service identities**: the paper execution gateway
   (`atp_exec_paper`) is its own process, container, and database role. No
   `atp_exec_live` package is created in Phase 1 (see ADR-008).
3. **Separate risk configuration** per mode, versioned and immutable
   (`core.risk_config`, keyed by `(mode, version)`).
4. **`mode` is explicit and non-null** on every execution-path entity:
   `TradeProposal`, `RiskDecision`, `OrderIntent`, `Order`, `Fill`,
   `Position`. Enforced by `CHECK (mode = 'PAPER')` / `CHECK (mode = 'LIVE')`
   on the respective schema's tables — the schema and the check are
   redundant by design, so either one failing still leaves the other.
5. **No foreign key crosses the `paper`/`live` schema boundary.** Reference
   data in `core.*` may be pointed to from both; `paper.*` may never point
   into `live.*` or vice versa.
6. **The `live` schema is created empty in Phase 1** (migration 0002), with
   no tables and no grants to any application role, so its inaccessibility
   is testable (`test_api_db_role_has_zero_privileges_on_live_schema`)
   before any live-path code exists, rather than only once it does.

## Consequences
Isolation is enforced at six independent layers (schema, grant, row-level
CHECK, referential, process, import-linter contract), documented in full in
the approved Phase 1 plan §6. No single layer failing is sufficient to make
a LIVE order reachable. The cost is duplicated DDL between `paper.*` and the
eventual `live.*` tables, accepted because it removes an entire class of
mode-confusion bugs that a shared-table-plus-discriminator design would
allow.

This supersedes the mode handling implied by the original
[docs/SCHEMAS.md](../SCHEMAS.md) sketch; that document is updated alongside
this ADR.
