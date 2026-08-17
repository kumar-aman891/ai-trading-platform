# `paper.trade_proposals`

## Purpose
The first typed artifact in the execution flow required by the user's
architecture: `AI/strategy → TradeProposal → RiskDecision → ApprovedOrderIntent
→ ExecutionGateway → Broker`. Created by a `paper_trader`-or-above actor (a
human, in Phase 1 — no strategy engine or AI proposer exists yet).

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `proposal_id` | `uuid` | no | PK, UUIDv7 |
| `mode` | `text` | no | `CHECK (mode = 'PAPER')` |
| `instrument_id` | `uuid` | no | FK → `core.instruments.instrument_id` |
| `side` | `text` | no | `CHECK (side IN ('BUY','SELL'))` |
| `quantity` | `numeric(20,6)` | no | `CHECK (quantity > 0)` |
| `order_type` | `text` | no | `CHECK (order_type IN ('MARKET','LIMIT'))` — Phase 1 supports these two only |
| `limit_price` | `numeric(20,6)` | yes | required iff `order_type = 'LIMIT'` |
| `trigger_price` | `numeric(20,6)` | yes | unused in Phase 1 (no stop-order types yet); column reserved |
| `product` | `text` | no | `CHECK (product IN ('CNC','MIS'))` — Phase 1 subset of Kite product types |
| `strategy_id` | `uuid` | yes | null in Phase 1 — no strategy registry exists |
| `strategy_version` | `integer` | yes | |
| `source_signal_id` | `uuid` | yes | null in Phase 1 — no signal engine exists |
| `client_request_id` | `text` | no | caller-supplied; anchors idempotency key derivation |
| `expected_risk` | `jsonb` | no | caller's own risk estimate — advisory only, never trusted by the risk engine |
| `created_by` | `uuid` | no | FK → `core.users.user_id` |
| `created_at` | `timestamptz` | no | |

## Constraints
- `CHECK ((order_type = 'LIMIT') = (limit_price IS NOT NULL))`
- `UNIQUE (client_request_id)` — first layer of duplicate-submission
  prevention (idempotency key derivation is documented on
  [order.md](order.md))

## Security boundary
Writable by `atp_api` (on behalf of an authenticated `paper_trader`+ user)
and readable by `atp_exec_paper`. `atp_exec_paper` never accepts proposal
*fields* directly from a caller — only a `proposal_id`, which it uses to
load this row itself (see [order_intent.md](order_intent.md) and the plan's
§4 module M5 execution sequence).

## Testing
`quantity`, `limit_price` are `Decimal` end-to-end (Python `Decimal` ↔
`NUMERIC`), never `float`, so P&L and sizing tests are deterministic
(rules/05-testing.md).
