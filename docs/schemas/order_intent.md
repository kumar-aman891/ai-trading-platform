# `paper.order_intents`

## Purpose
`ApprovedOrderIntent` — the artifact from [ADR-008](../adr/ADR-008-order-intent-minting.md)
that sits between an approved `RiskDecision` and the execution gateway's
broker-facing call. This is the mechanism that makes "the execution gateway
must not accept arbitrary order parameters directly from an AI or generic
API caller" true at the type level, not by convention.

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `intent_id` | `uuid` | no | PK, UUIDv7 |
| `mode` | `text` | no | `CHECK (mode = 'PAPER')` |
| `decision_id` | `uuid` | no | FK → `paper.risk_decisions.decision_id` |
| `proposal_id` | `uuid` | no | FK → `paper.trade_proposals.proposal_id` (denormalized for query convenience; must match `risk_decisions.proposal_id` for the same `decision_id`, enforced at mint time in code, not by a DB constraint that would require a cross-row check) |
| `canonical_payload` | `jsonb` | no | the *only* order parameters that exist for this intent — instrument, side, quantity, order type, price fields, product |
| `payload_hash` | `text` | no | `sha256` of canonicalized `canonical_payload` |
| `minted_at` | `timestamptz` | no | |
| `expires_at` | `timestamptz` | no | `minted_at + 30s` default |

## Constraints
- `UNIQUE (decision_id)` — an intent is minted from a decision **at most
  once**, ever. Single-use by construction.
- Only ever inserted by `atp_domain.risk.engine`'s restricted minting
  function, called from `atp_exec_paper`. No API route writes this table.

## Security boundary
This table's write path is the narrowest in the schema. `atp_api` has no
`INSERT` grant on it at all — only `atp_exec_paper` does (Phase 1 Step 9,
ADR-011), and even there, only through
`atp_domain.risk.engine.mint_intent_for_decision` - the single capability-
gated minting call site (`atp_domain.intents`'s module docstring), enforced
by `test_approved_intent_minted_only_by_risk_engine` and
`test_atp_exec_paper_never_imports_the_low_level_minting_primitives`
(`tests/safety/test_no_execution_path_in_atp_exec_paper.py`).

## Testing
`test_intent_is_single_use_under_concurrency` (Phase 1 Step 9, ADR-011)
submits the same `decision_id` concurrently and asserts exactly one
`order_intents` row and exactly one downstream `paper.orders` row result —
the `UNIQUE
(decision_id)` constraint is the mechanism, the test is the proof.
