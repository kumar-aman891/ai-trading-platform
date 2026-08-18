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
API route - migration `0004_paper_cash_ledger_seed` inserts one
`PAPER_INITIAL_CAPITAL = 10,000,000` (`Decimal("10000000.000000")`)
`DEPOSIT` row, a deliberately conservative fixture value (ten times
migration 0001's `_BOOTSTRAP_MAX_ORDER_NOTIONAL`), not derived from any
spec. Not configurable by design - there is no environment variable for it
and none is planned; changing it means changing the migration.

`PAPER_INITIAL_CAPITAL` is:
- a Phase-1 deterministic paper-trading fixture, chosen only to make paper
  execution reproducible (`RISK.CAPITAL.001`/`SimulatedCashSufficiencyRule`
  needs a real, non-`None` balance to evaluate a proposal against at all);
- **not** a real brokerage/account balance - no broker connection exists
  in Phase 1 for one to reflect (ADR-006, ADR-008);
- **not** a production risk limit - `core.risk_config.max_order_notional`
  is the risk limit; this is only the simulated cash the one fixture
  account starts with;
- **not** an assumption about any real user's capital - see the "Note"
  section below (one simulated account, shared by every `paper_trader`+
  user).

## Note
This ledger is intentionally simple — a running balance, not a
double-entry general ledger — because Phase 1 has exactly one simulated
cash account per mode and no margin/leverage modelling. Revisit if Phase 4+
introduces margin products.
