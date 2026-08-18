# `paper.fills`

## Purpose
Simulated fill records. **Deliberately fake in Phase 1** — see
`atp_exec_paper.simulator`: immediate full fill at the proposal's limit
price, no slippage, no partial fills, no latency. A MARKET proposal never
reaches the simulator at all — Phase 1 has no market-data adapter, so no
canonical reference price exists for one to be filled at
(`RISK.DATA.001`/`atp_domain.risk.catalog.data_rules.PricedReferenceRule`,
Phase 1 Step 9 / ADR-011: MARKET is deterministically `INDETERMINATE`,
which the risk engine's reject-by-default aggregation turns into
`REJECTED`, before any fill could be written). No row in this table is ever
produced for a MARKET proposal, and no caller-supplied reference price is
ever accepted or invented. Every row and every API response built from it
carries `simulated = true`.

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `fill_id` | `uuid` | no | PK, UUIDv7 |
| `mode` | `text` | no | `CHECK (mode = 'PAPER')` |
| `internal_order_id` | `uuid` | no | FK → `paper.orders.internal_order_id` |
| `broker_trade_id` | `text` | yes | always null in Phase 1 |
| `quantity` | `numeric(20,6)` | no | `CHECK (quantity > 0)` |
| `price` | `numeric(20,6)` | no | |
| `fees` | `numeric(20,6)` | no | `0` in Phase 1 — the fake simulator does not model fees |
| `taxes` | `numeric(20,6)` | no | `0` in Phase 1 — not modelled |
| `simulated` | `boolean` | no | always `true` in Phase 1; non-optional column so a future real fill (Phase 4+) is structurally distinguishable from a simulated one |
| `source` | `text` | no | `'PAPER_SIMULATOR'` |
| `filled_at` | `timestamptz` | no | |

## Constraints
- `CHECK (fees >= 0 AND taxes >= 0)`

## Security boundary
Written only by `atp_exec_paper`. No update path exists post-insert —
fills, once recorded, are corrected by a new offsetting entry if ever
needed (not implemented in Phase 1; there is no reversal flow yet).

## Honesty requirement
Per docs/BACKTESTING.md ("Do not present either as equivalent to live
performance"), every DTO surfacing a `Fill` in the API sets
`simulated: true` as a required field, not an optional flag that could be
omitted. The UI displays a persistent "simulated fills — not
representative" notice wherever fills are shown (plan §17, §18).
