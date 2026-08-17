# ADR-010: Operational State Stays Relational; Audit Stays Append-Only. Not Fully Event-Sourced.

## Status
Accepted — Phase 1.

## Context
[.claude/rules/01-architecture.md](../../.claude/rules/01-architecture.md)
asks for "append-only event records for decisions, orders, fills, risk
decisions, and AI tool calls." Taken maximally, that could be read as a
mandate for full event sourcing, where current state (a position, an order's
status) is derived by folding over an event log rather than stored directly.
No concrete Phase 1 requirement demonstrates a need for that: nothing calls
for event replay, temporal queries against historical state, or
CQRS-style read models.

## Decision
Two distinct kinds of state, kept structurally separate:

1. **Operational state** — `core.*`, `paper.*`, (later) `live.*`. Ordinary
   mutable relational rows: `paper.positions` is a row that gets updated,
   `paper.orders.status` is a column that transitions through an explicit
   FSM. Normal transactional reads and writes.
2. **Immutable audit/event history** — `audit.audit_events`. Append-only,
   enforced by both revoked `UPDATE`/`DELETE`/`TRUNCATE` grants and a
   rejecting trigger. Every state-changing action in (1) writes a
   corresponding row in (2) **in the same transaction**, so a committed
   state change without its audit record is impossible — but the audit
   record is a *log of what happened*, not the *source* current state is
   derived from.

`RiskDecision`, `Order`→`Fill` history, and `ApprovedOrderIntent` are
themselves append-only-by-nature (a decision, once made, is never edited;
an intent is single-use) and live in `paper.*`/`live.*` for that reason —
this is narrower than event sourcing, since positions and order status
remain simple mutable projections, not folds over an event stream.

## Consequences
The system is **not** fully event-sourced, and should not become so unless
a concrete Phase 2+ requirement (e.g., point-in-time portfolio
reconstruction for backtesting) demonstrates the need — at which point it
would be evaluated as a scoped addition for that specific read model, not a
platform-wide rewrite. This keeps Phase 1 persistence code ordinary and
testable: repositories query current rows directly, and the audit trail is
consulted for provenance and investigation, not for computing current
state.
