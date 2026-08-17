# `core.risk_config`

## Purpose
Versioned, immutable risk configuration per mode. `RiskDecision` binds to
the exact config version that produced it, so an evaluation is always
reproducible. Per docs/RISK_AND_GUARDRAILS.md: "Risk configuration is
immutable per run and versioned."

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `risk_config_id` | `uuid` | no | PK, UUIDv7 |
| `mode` | `text` | no | `CHECK (mode IN ('PAPER','LIVE'))` |
| `version` | `integer` | no | monotonically increasing per mode |
| `config` | `jsonb` | no | rule-level limits (per-order notional, daily loss, concentration, etc.) |
| `config_hash` | `text` | no | `sha256` of canonicalized `config`, for `RiskDecision.limit_snapshot_hash` binding |
| `active` | `boolean` | no | exactly one active row per mode |
| `created_at` | `timestamptz` | no | |
| `created_by` | `uuid` | no | FK → `core.users.user_id` — always `administrator` |

## Constraints
- `UNIQUE (mode, version)`
- `UNIQUE (mode) WHERE active` (partial unique index — enforces exactly one
  active config per mode)
- **No `UPDATE` on `config`, `config_hash`, `mode`, `version` after insert**
  (enforced by trigger, mirroring the audit-table pattern from ADR-010,
  though this table is not itself append-only for the `active` flag —
  activating a new version inserts a row and flips `active`, never edits
  history).

## Security boundary
No API route in Phase 1 mutates this table (`config/PLATFORM_DEFAULTS.md`:
"modify risk limits: blocked for AI" — enforced here by having no mutating
route at all, for any actor). Phase 1 ships one migration-seeded PAPER
config row; there is no admin UI for authoring new versions yet.

## Phase 1 rule set
See [docs/adr/ADR-005](../adr/ADR-005-paper-live-isolation.md) and the risk
engine skeleton (Phase 1 Step 11): six PAPER rules are genuinely
implemented (mode match, kill-switch state, order quantity/lot/tick,
order-type/price coherence, max notional, simulated cash sufficiency); all
LIVE rules are `INDETERMINATE` stubs, which the aggregator collapses to
`REJECT`.
