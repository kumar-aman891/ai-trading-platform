# `paper.orders`

## Purpose
The order as recorded once an `ApprovedOrderIntent` has been acted on by
the (fake, in Phase 1) simulator. `internal_order_id` is the platform's own
identifier; `broker_order_id` is null in Phase 1 (no real broker
involved).

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `internal_order_id` | `uuid` | no | PK, UUIDv7 |
| `mode` | `text` | no | `CHECK (mode = 'PAPER')` |
| `broker_order_id` | `text` | yes | always null in Phase 1 — no broker adapter exists |
| `broker_provider` | `text` | yes | always null in Phase 1; paired with `broker_order_id` per rules/01 |
| `proposal_id` | `uuid` | no | FK → `paper.trade_proposals.proposal_id` |
| `intent_id` | `uuid` | no | FK → `paper.order_intents.intent_id` |
| `idempotency_key` | `text` | no | see derivation below |
| `status` | `text` | no | `CHECK (status IN ('SUBMITTED','FILLED','REJECTED','CANCELLED'))` — Phase 1's simulator only ever produces `SUBMITTED` → `FILLED` immediately; `REJECTED`/`CANCELLED` are reserved for when the simulator gains realism |
| `submitted_at` | `timestamptz` | no | |
| `acknowledged_at` | `timestamptz` | yes | equals `submitted_at` for the fake simulator (no latency modelled) |
| `last_update_at` | `timestamptz` | no | |

## Idempotency key derivation
```
idempotency_key = sha256(
    mode || client_request_id || instrument_id || side ||
    quantity || order_type || limit_price || product
)
```
`client_request_id` comes from the originating `TradeProposal`. The
`UNIQUE (idempotency_key)` constraint below makes duplicate-order
prevention a database guarantee, not an application-logic check that could
race.

## Constraints
- `UNIQUE (proposal_id)` — one order per proposal, ever
- `UNIQUE (intent_id)` — one order per intent, ever (mirrors the intent's
  own single-use guarantee)
- `UNIQUE (idempotency_key)`

## Security boundary
Written only by `atp_exec_paper`, inside the same transaction as the
corresponding `paper.fills`/`paper.positions` update and the
`audit.audit_events` insert (module M5's execution sequence, plan §4).
`atp_api` has read-only access for the paper ledger view.

## Testing
`test_duplicate_proposal_submission_creates_exactly_one_order` (Phase 1
Step 14) submits the same `proposal_id` twice concurrently and asserts a
single `orders` row — exercising all three uniqueness constraints above at
once.
