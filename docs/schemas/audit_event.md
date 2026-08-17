# `audit.audit_events`

## Purpose
The immutable event/decision ledger required by `.claude/rules/01-architecture.md`
("prefer append-only event records for decisions, orders, fills, risk
decisions, and AI tool calls") and specified field-by-field in
[docs/OBSERVABILITY.md](../OBSERVABILITY.md).

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `event_id` | `uuid` | no | PK, UUIDv7 |
| `correlation_id` | `uuid` | no | one ID from HTTP request to this row, propagated via ASGI middleware + contextvar |
| `occurred_at` | `timestamptz` | no | when the action happened |
| `recorded_at` | `timestamptz` | no | when this row was written (usually equal; differs under replay/backfill, none of which exist in Phase 1) |
| `actor_type` | `text` | no | `CHECK (actor_type IN ('USER','AGENT','SYSTEM','BROKER'))` |
| `actor_id` | `text` | yes | hashed/opaque identifier — never a raw account identifier (docs/SECURITY.md data-minimization rule) |
| `action` | `text` | no | e.g. `PROPOSAL_CREATED`, `RISK_DECISION_RECORDED`, `INTENT_MINTED`, `ORDER_SUBMITTED`, `KILL_SWITCH_ENGAGED`, `LOGIN_SUCCEEDED` |
| `mode` | `text` | yes | `CHECK (mode IS NULL OR mode IN ('PAPER','LIVE'))` — null only for mode-agnostic system events (e.g. login) |
| `strategy_id` | `uuid` | yes | |
| `strategy_version` | `integer` | yes | |
| `instrument_id` | `uuid` | yes | FK → `core.instruments.instrument_id` |
| `source_refs` | `jsonb` | yes | evidence references, not payloads (rules/01) |
| `input_hash` | `text` | yes | binds this event to its exact inputs without duplicating them |
| `decision` | `text` | yes | e.g. `APPROVED`, `REJECTED` |
| `risk_rule_ids` | `text[]` | yes | every rule ID evaluated, pass or fail |
| `broker_order_id` | `text` | yes | |
| `broker_provider` | `text` | yes | required together with `broker_order_id` (rules/01: external IDs always paired with provider) |
| `error_code` | `text` | yes | |
| `error_class` | `text` | yes | |
| `payload` | `jsonb` | yes | redacted at write time by the same processor used for logs |

## Constraints
- `CHECK (broker_order_id IS NULL) = (broker_provider IS NULL)` — the pair
  is present together or not at all.
- **Append-only**: `REVOKE UPDATE, DELETE, TRUNCATE ON audit.audit_events
  FROM PUBLIC` and all application roles; a `BEFORE UPDATE OR DELETE`
  trigger raises unconditionally as a second, independent enforcement
  layer.

## Indexes
- `INDEX (correlation_id)`
- `INDEX (occurred_at)`
- `INDEX (mode, action, occurred_at)` — the audit browser's primary filter path
- `INDEX (instrument_id) WHERE instrument_id IS NOT NULL`

## Security boundary
The single most security-relevant table in the schema. `payload` is
redacted by the exact same `atp_platform.redaction` processor the logger
uses — one implementation, two consumers, one test
(`test_secret_never_appears_in_logs` and its audit-table counterpart, Phase
1 Step 3).

## Design note
Every state-changing write elsewhere in the schema inserts its
corresponding `audit.audit_events` row **in the same transaction** — a
committed order without its audit event is a database-level impossibility,
not a code-review expectation (`test_audit_event_and_state_change_share_a_transaction`,
Phase 1 Step 9). Hash-chaining between successive events (tamper
*evidence*, as opposed to the tamper *prevention* the grant+trigger model
already provides) is deliberately deferred — see ADR-010.
