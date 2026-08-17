# `core.instruments`

## Purpose
Instrument master. Mode-agnostic reference data shared by `paper` and
(later) `live`. Phase 1 seeds a small fixture set — no Kite instrument
loader exists yet (docs/DATA_SOURCES.md's daily-refresh loader is a Phase 2
data-plane module).

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `instrument_id` | `uuid` | no | PK, UUIDv7 — internal identifier, stable across provider changes |
| `provider` | `text` | no | e.g. `FIXTURE` in Phase 1; `KITE` once a real loader exists |
| `provider_instrument_token` | `text` | no | the provider's own token/ID |
| `exchange` | `text` | no | e.g. `NSE`, `BSE` |
| `segment` | `text` | no | e.g. `EQ`, `FO` |
| `symbol` | `text` | no | |
| `name` | `text` | no | |
| `expiry` | `date` | yes | derivatives only |
| `strike` | `numeric(20,6)` | yes | options only |
| `option_type` | `text` | yes | `CHECK (option_type IN ('CE','PE') OR option_type IS NULL)` |
| `lot_size` | `integer` | no | |
| `tick_size` | `numeric(20,6)` | no | |
| `active_from` | `timestamptz` | no | |
| `active_to` | `timestamptz` | yes | null while active |

## Constraints
- `UNIQUE (provider, provider_instrument_token)`
- `UNIQUE (exchange, segment, symbol, expiry, strike, option_type)` where
  `active_to IS NULL` (one currently-active row per logical instrument)

## Security boundary
None beyond standard read access — instrument metadata is not sensitive.
Per `.claude/rules/04-ai.md`, instrument names/symbols are still treated as
**untrusted text** wherever they originate from an external provider feed —
this table stores them, it does not sanitize them for prompt-injection
purposes; that is the AI tool layer's responsibility (not built in Phase 1).

## Phase 1 note
Rows are `provider = 'FIXTURE'`, ~20 seeded NSE equities, inserted by a
seed migration. This keeps provenance honest at the row level — nothing
claims to be live Kite data.
