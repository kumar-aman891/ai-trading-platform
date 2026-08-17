# `paper.cash_ledger`

## Purpose
Simulated cash balance movements for the paper account. Backs the
`CAPITAL.001` risk rule ("simulated cash sufficiency" — the one genuinely
implemented capital check in Phase 1, per the plan's §11.3).

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `entry_id` | `uuid` | no | PK, UUIDv7 |
| `mode` | `text` | no | `CHECK (mode = 'PAPER')` |
| `entry_type` | `text` | no | `CHECK (entry_type IN ('DEPOSIT','FILL_DEBIT','FILL_CREDIT'))` — Phase 1 subset; `DEPOSIT` seeds starting simulated capital, `FILL_DEBIT`/`FILL_CREDIT` post fill cash effects |
| `amount` | `numeric(20,6)` | no | always positive; sign is implied by `entry_type` |
| `related_fill_id` | `uuid` | yes | FK → `paper.fills.fill_id`; null for `DEPOSIT` |
| `balance_after` | `numeric(20,6)` | no | running balance, computed at insert time within the same transaction as the triggering fill |
| `created_at` | `timestamptz` | no | |

## Constraints
- `CHECK (amount > 0)`
- `CHECK ((entry_type = 'DEPOSIT') = (related_fill_id IS NULL))`

## Security boundary
Written only by `atp_exec_paper`. The starting `DEPOSIT` is
migration-seeded per paper account, not user-configurable via any Phase 1
API route.

## Note
This ledger is intentionally simple — a running balance, not a
double-entry general ledger — because Phase 1 has exactly one simulated
cash account per mode and no margin/leverage modelling. Revisit if Phase 4+
introduces margin products.
