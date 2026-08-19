# `core.sessions`

## Purpose
Server-side session state backing the HttpOnly cookie auth model (see the
plan's §9 authentication design). The cookie carries only an opaque session
ID; all session state lives here.

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `session_id_hash` | `text` | no | PK — SHA-256 of the opaque session ID; the raw ID is never stored |
| `user_id` | `uuid` | no | FK → `core.users.user_id` |
| `csrf_token` | `text` | no | double-submit CSRF token bound to this session |
| `created_at` | `timestamptz` | no | |
| `expires_at` | `timestamptz` | no | sliding TTL, default +8h from last use |
| `revoked_at` | `timestamptz` | yes | set on logout or administrative revocation |
| `ip_address` | `inet` | yes | for anomaly review only, never used as an auth factor |

## Indexes
- `INDEX (user_id)` — supports "revoke all sessions for user"
- `INDEX (expires_at)` — supports `atp_worker`'s `SESSION_REAP` job's
  expired-session count query (ADR-013 §2)

## Constraints
- Row is looked up by `sha256(cookie_value)`; the raw session ID is never
  persisted anywhere, including logs (redaction denylist covers
  `session_id`/`cookie`).

## Security boundary
Readable/writable by `atp_api` only. `atp_worker` has read-only access
scoped to `(session_id_hash, expires_at, revoked_at)` — it does not need
`csrf_token` or `user_id` and is not granted them.

**Correction (ADR-013 §1, Phase 1 Step 12 Phase B):** this column-scoped
grant does not back a "reaper" in the sense of a job that revokes or
deletes sessions. `SESSION_REAP` is observation-only: it counts expired,
unrevoked sessions and emits a log line, never writing to this table.
`atp_worker` holds no INSERT/UPDATE/DELETE grant here and none is planned
- there is no security gap this leaves open, since
`validate_and_renew_session` already refuses an expired session regardless
of `revoked_at`. The grant and the index above were always this narrow;
only earlier wording describing them as backing an active "reaper" was
wrong, and is corrected here rather than in code.

## Testing
Expiry and revocation are exercised via `atp_domain.clock.Clock` injection
(`FrozenClock`), not wall-clock sleeps, per rules/05-testing.md.
