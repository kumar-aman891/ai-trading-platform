# `core.job_queue`

## Purpose
Durable job table backing `atp_worker`, per docs/TECH_STACK.md: "Start with
one worker using a durable job table or task queue" — no Celery, no message
broker in Phase 1.

## Columns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `job_id` | `uuid` | no | PK, UUIDv7 |
| `job_type` | `text` | no | `CHECK (job_type IN ('SESSION_REAP','AUDIT_INTEGRITY_CHECK','RETENTION'))` — Phase 1 job types only |
| `payload` | `jsonb` | no | job-specific parameters |
| `status` | `text` | no | `CHECK (status IN ('PENDING','RUNNING','SUCCEEDED','FAILED'))` |
| `attempts` | `integer` | no | default `0` |
| `max_attempts` | `integer` | no | default `3` |
| `scheduled_for` | `timestamptz` | no | |
| `locked_at` | `timestamptz` | yes | set when a worker claims the row |
| `locked_by` | `text` | yes | worker instance identifier |
| `completed_at` | `timestamptz` | yes | |
| `last_error` | `text` | yes | redacted before storage — same redaction pipeline as logs |
| `created_at` | `timestamptz` | no | |

## Indexes
- `INDEX (status, scheduled_for) WHERE status = 'PENDING'` — supports the
  poll query

## Access pattern
Workers claim rows via
`SELECT ... FOR UPDATE SKIP LOCKED WHERE status = 'PENDING' AND scheduled_for <= now() ORDER BY scheduled_for LIMIT 1`,
per the plan's §6 module design for `atp_worker`. Idempotent re-execution is
required: a crash mid-job must leave no partial state (tested in Phase 1
Step 16).

## Security boundary
Read/write: `atp_worker` only. `atp_worker` holds **no** privileges on
`paper.orders` or any execution-path table — its job types in Phase 1 never
touch order state.
