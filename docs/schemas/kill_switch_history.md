# `core.kill_switch_history`

## Purpose
Every kill-switch transition, ever. `core.kill_switch_state` holds only the
current value; this table is the operational record of how it got there
(distinct from, and in addition to, the `audit.audit_events` row written in
the same transaction — see [ADR-010](../adr/ADR-010-operational-vs-audit-state.md)
on why both exist: this table is queryable per-switch history for the UI,
`audit.audit_events` is the cross-cutting immutable log).

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `history_id` | `uuid` | no | PK, UUIDv7 |
| `switch_id` | `text` | no | FK → `core.kill_switch_state.switch_id` |
| `previous_engaged` | `boolean` | no | |
| `new_engaged` | `boolean` | no | |
| `changed_at` | `timestamptz` | no | |
| `changed_by` | `uuid` | yes | FK → `core.users.user_id`; null for system-initiated (e.g. fail-closed) |
| `reason` | `text` | no | |
| `audit_event_id` | `uuid` | no | FK → `audit.audit_events.event_id` — links the two records |

## Constraints
- Append-only by convention and by the same trigger pattern as
  `audit.audit_events` (revoked `UPDATE`/`DELETE` grants) — this table is
  itself a decision record and should never be edited after the fact.
- Insert happens in the **same transaction** as the `core.kill_switch_state`
  update and the `audit.audit_events` insert.

## Security boundary
Read access: `atp_api` (for the kill-switch admin UI's history view).
Write access: only via the same code path that updates
`core.kill_switch_state` — never independently.
