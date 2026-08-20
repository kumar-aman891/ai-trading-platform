# `paper.trade_proposals`

## Purpose
The first typed artifact in the execution flow required by the user's
architecture: `AI/strategy → TradeProposal → RiskDecision → ApprovedOrderIntent
→ ExecutionGateway → Broker`. Created by either a `paper_trader`-or-above
human actor or a registered strategy (ADR-014, ADR-015) — no strategy
runtime submits a proposal yet as of this schema revision, but the
attribution shape below already accommodates one.

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
| `strategy_id` | `uuid` | yes | set for a strategy-authored proposal (ADR-015); null for a human-authored one |
| `strategy_version` | `integer` | yes | set alongside `strategy_id` |
| `source_signal_id` | `uuid` | yes | null in Phase 1 — no signal engine exists |
| `client_request_id` | `text` | no | caller-supplied; anchors idempotency key derivation |
| `expected_risk` | `jsonb` | no | caller's own risk estimate — advisory only, never trusted by the risk engine |
| `created_by` | `uuid` | **yes** (ADR-015) | FK → `core.users.user_id`; `NULL` for a strategy-authored proposal |
| `created_at` | `timestamptz` | no | |

## Constraints
- `CHECK ((order_type = 'LIMIT') = (limit_price IS NOT NULL))`
- `CHECK (created_by IS NOT NULL OR strategy_id IS NOT NULL)` —
  `proposal_has_an_author` (migration 0006, ADR-015): every proposal is
  attributable to exactly a human (`created_by`) or a strategy
  (`strategy_id`), never neither. `created_by` follows the same
  NULL-for-non-human-actor convention already established by
  `core.risk_config.created_by`/`core.kill_switch_state.updated_by` — no
  `core.users` row is ever created to represent a strategy
  (`docs/schemas/user.md`'s "no seeded user, ever" rule).
- `UNIQUE (client_request_id)` — first layer of duplicate-submission
  prevention (idempotency key derivation is documented on
  [order.md](order.md))

## Security boundary
Writable by `atp_api` (on behalf of an authenticated `paper_trader`+ user)
and readable by `atp_exec_paper`. `atp_exec_paper` never accepts proposal
*fields* directly from a caller — only a `proposal_id`, which it uses to
load this row itself (see [order_intent.md](order_intent.md) and the plan's
§4 module M5 execution sequence).

**Real writer (Phase 1 Step 10, ADR-012):**
`POST /api/v1/paper/proposals` (`atp_api.routers.paper`,
`atp_api.services.paper_proposals`) requires `Permission.SUBMIT_PAPER_PROPOSAL`
and performs **structural validation only** — it never evaluates risk,
never calls `atp_domain.risk.engine.evaluate`/`mint_intent_for_decision`,
never imports `atp_domain.intents` at all, and is not gated on the kill switch (`docs/adr/ADR-012-proposal-intake-is-not-a-risk-gate.md`).
`mode`, `proposal_id`, and `created_at` are always server-set;
`created_by` is the authenticated principal's `user_id` — this route never
supplies `strategy_id`, so every proposal it creates is human-attributed.
A 2xx response
means *recorded*, never *approved* — the risk decision is written
separately, later, by `atp_exec_paper`'s claim loop (ADR-011). Read back via
`GET /api/v1/paper/proposals`/`GET /api/v1/paper/proposals/{proposal_id}`,
which nest the resulting `RiskDecision`/`Order`/`Fill` (if any exist yet).

## Testing
`quantity`, `limit_price` are `Decimal` end-to-end (Python `Decimal` ↔
`NUMERIC`), never `float`, so P&L and sizing tests are deterministic
(rules/05-testing.md).
