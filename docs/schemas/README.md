# Phase 1 Schema Specifications

Concrete types, keys, indexes, nullability, and enum vocabularies for every
Phase 1 entity, organized by PostgreSQL schema per
[ADR-005](../adr/ADR-005-paper-live-isolation.md). This is the input to
migrations 0001 (`core` + `audit` + `paper`) and 0002 (`live`, empty), which
are Phase 1 Steps 6–7 and not part of this document set.

[docs/SCHEMAS.md](../SCHEMAS.md) remains the conceptual sketch; these files
are its concrete counterpart.

## Conventions (apply to every table below)

- All primary keys: `uuid`, generated application-side as UUIDv7 via
  `atp_domain.ids.IdGenerator` (time-ordered, so PK order approximates
  insertion order without leaking a sequence).
- All timestamps: `timestamptz`, written in UTC.
- All money/quantity columns: `NUMERIC(20,6)`. `float`/`double precision` is
  never used for money or quantity anywhere in this schema — enforced by a
  CI grep (`make arch-check`).
- All enum-like columns: `TEXT` + `CHECK (... IN (...))`, not native
  Postgres `ENUM` types — alterable without a table rewrite.
- Every external ID is stored together with its provider
  (`broker_order_id` + `broker_provider`, `provider_instrument_token` +
  `provider`), per `.claude/rules/01-architecture.md`.
- `mode` is `TEXT NOT NULL CHECK (mode = 'PAPER')` on every table in the
  `paper` schema, and `CHECK (mode = 'LIVE')` on the (currently
  nonexistent) `live` schema equivalents — redundant with the schema
  boundary itself, by design (ADR-005 §6).

## Schema → entity map

| PostgreSQL schema | Entities | Mutability |
|---|---|---|
| `core` | [User](user.md), [Session](session.md), [Instrument](instrument.md), [RiskConfig](risk_config.md), [KillSwitchState](kill_switch_state.md), [KillSwitchHistory](kill_switch_history.md), [JobQueue](job_queue.md) | operational, mutable |
| `audit` | [AuditEvent](audit_event.md) | **append-only, immutable** (ADR-010) |
| `paper` | [TradeProposal](trade_proposal.md), [RiskDecision](risk_decision.md), [ApprovedOrderIntent](order_intent.md), [Order](order.md), [Fill](fill.md), [Position](position.md), [CashLedger](cash_ledger.md) | operational; decisions/intents append-only by nature |
| `live` | *(none — schema created empty and ungranted in Phase 1, ADR-005 §5.4)* | n/a |

`AIEvent` and `MarketBar` are documented conceptually in
[docs/SCHEMAS.md](../SCHEMAS.md) but have no concrete spec here — no LLM
provider and no market-data adapter exist in Phase 1, so there is nothing
yet to size these tables against.
