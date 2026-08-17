# `paper.positions`

## Purpose
Current position per instrument in PAPER mode. An ordinary mutable
relational row, updated on each fill — **not** derived by folding over an
event log (see [ADR-010](../adr/ADR-010-operational-vs-audit-state.md): the
platform is not event-sourced).

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `position_id` | `uuid` | no | PK, UUIDv7 |
| `mode` | `text` | no | `CHECK (mode = 'PAPER')` |
| `instrument_id` | `uuid` | no | FK → `core.instruments.instrument_id` |
| `quantity` | `numeric(20,6)` | no | signed; negative = short (Phase 1's simulator does not model short-sale eligibility, so this is a schema allowance, not a Phase 1 capability claim) |
| `average_price` | `numeric(20,6)` | no | |
| `realized_pnl` | `numeric(20,6)` | no | default `0` |
| `unrealized_pnl` | `numeric(20,6)` | no | default `0` — computed against the last known reference price; no live market data exists in Phase 1, so this is only ever recomputed at fill time from the fill price itself |
| `updated_at` | `timestamptz` | no | |

## Constraints
- `UNIQUE (mode, instrument_id)` — one position row per instrument per mode

## Security boundary
Written only by `atp_exec_paper`, in the same transaction as the triggering
`paper.fills` insert. Read by `atp_api` for the portfolio view.

## Testing
Position math (average price on adds, realized P&L on reductions) is
`Decimal`-only and covered by deterministic unit tests in
`atp_domain` before any persistence code exists (rules/05-testing.md:
"Any bug involving an order or P&L calculation gets a regression test
before the fix is considered complete").
