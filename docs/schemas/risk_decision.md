# `paper.risk_decisions`

## Purpose
The deterministic risk engine's output for a `TradeProposal`. Written for
**every** evaluation — approvals and rejections alike — never only on
success.

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `decision_id` | `uuid` | no | PK, UUIDv7 |
| `mode` | `text` | no | `CHECK (mode = 'PAPER')` |
| `proposal_id` | `uuid` | no | FK → `paper.trade_proposals.proposal_id` |
| `outcome` | `text` | no | `CHECK (outcome IN ('APPROVED','REJECTED'))` — `INDETERMINATE` never persists; the aggregator collapses it to `REJECTED` before this row is written |
| `rule_results` | `jsonb` | no | array of `{rule_id, outcome, message, evidence}` for **every** rule evaluated |
| `risk_config_id` | `uuid` | no | FK → `core.risk_config.risk_config_id` — the exact config version used |
| `limit_snapshot_hash` | `text` | no | `= core.risk_config.config_hash` at evaluation time, duplicated here for a decision to be self-verifying without a join |
| `decided_at` | `timestamptz` | no | |

## Constraints
- `UNIQUE (proposal_id)` — one decision per proposal, ever. A proposal is
  never re-evaluated in place; a corrected proposal is a new
  `TradeProposal` row.

## Security boundary
Written only by `atp_exec_paper` (the risk engine runs in-process within
the executor — see [docs/ARCHITECTURE.md](../ARCHITECTURE.md) §2). Readable
by `atp_api` for the risk-decision detail view. No route allows any actor
to create or edit a row here directly — a `RiskDecision` is always a
side-effect of evaluating a `TradeProposal`, never an independent write.

## Testing
`test_live_proposal_can_never_be_approved` and
`test_any_indeterminate_causes_overall_reject`
(`tests/unit/domain/test_risk_engine.py`, Phase 1 Step 4) exercise this
table's invariants directly: a LIVE-mode
proposal's decision (once `live.risk_decisions` exists, Phase 4) has
`outcome = 'REJECTED'` with every `rule_results` entry either `REJECT` or
the pre-aggregation `INDETERMINATE` in the evidence detail.
