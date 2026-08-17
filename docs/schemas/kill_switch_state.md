# `core.kill_switch_state`

## Purpose
Current state of the six switches from
[ADR-007](../adr/ADR-007-kill-switch-taxonomy.md). Source of truth for the
fail-closed kill-switch policy; a short-TTL cache may sit in front of reads
but this table is authoritative.

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `switch_id` | `text` | no | PK — `GLOBAL_LIVE`, `LIVE_ACCOUNT`, `PAPER`, `STRATEGY:{id}`, `INSTRUMENT:{id}`, `API_EXECUTION` |
| `engaged` | `boolean` | no | |
| `updated_at` | `timestamptz` | no | |
| `updated_by` | `uuid` | yes | FK → `core.users.user_id`; null for system/migration-seeded rows |
| `reason` | `text` | yes | required (`NOT NULL`) when `updated_by` is set — human or system rationale |

## Constraints
- Row-level `CHECK`: `updated_by IS NULL OR reason IS NOT NULL` — a human
  or agent-initiated change always carries a reason.
- Seeded rows at migration time: `GLOBAL_LIVE` and `LIVE_ACCOUNT` →
  `engaged = true`; `PAPER`, `API_EXECUTION` → `engaged = false`.
  `STRATEGY:*`/`INSTRUMENT:*` rows are created on demand, defaulting to
  `engaged = false`.

## Security boundary
Read by every service (`atp_api`, `atp_exec_paper`, `atp_worker`) — the
kill switch must be checkable everywhere an order could originate. Write
access is `atp_api`-only, gated by role: engaging requires `paper_trader`+,
disengaging requires `administrator` (ADR-007's deliberate asymmetry).

## Fail-closed behavior
If this table is unreachable, the row for a given `switch_id` is missing,
or `engaged` is unparseable, `atp_domain.killswitch.KillSwitchPolicy`
returns `ENGAGED` — never a default guess. See
[kill_switch_history.md](kill_switch_history.md) for the audit trail of
transitions.
