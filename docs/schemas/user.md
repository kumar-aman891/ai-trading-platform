# `core.users`

## Purpose
Authenticated principals. Backs authentication and the five-role RBAC model
from [docs/SECURITY.md](../SECURITY.md).

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `user_id` | `uuid` | no | PK, UUIDv7 |
| `username` | `text` | no | unique, case-insensitive (`citext` or lower() unique index) |
| `password_hash` | `text` | no | Argon2id; never logged, never returned by any API response |
| `role` | `text` | no | `CHECK (role IN ('viewer','researcher','paper_trader','live_trader','administrator'))` |
| `is_active` | `boolean` | no | default `true`; disabling a user does not delete history |
| `must_change_password` | `boolean` | no | default `false`; set `true` for bootstrap admin |
| `created_at` | `timestamptz` | no | |
| `updated_at` | `timestamptz` | no | |

## Constraints
- `UNIQUE (lower(username))`
- No default/seeded user in any migration (asserted by
  `test_settings_refuse_to_start_with_live_mode_or_broker_credentials`'s
  sibling bootstrap test, Phase 1 Step 8).

## Security boundary
Holds `password_hash` only — never a plaintext password, never a session
token. Readable by `atp_api` only; no other service role has SELECT.

## Notes
`live_trader` is assignable in Phase 1 but grants nothing, since no LIVE
route or LIVE-scoped API exists to authorize (ADR-005, ADR-006).
