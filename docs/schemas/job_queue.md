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
| `attempts` | `integer` | no | default `0`; `CHECK (attempts >= 0 AND attempts <= max_attempts AND max_attempts >= 1)` (migration `0005_job_queue_claim_constraints`, ADR-013) |
| `max_attempts` | `integer` | no | default `3`; see the `attempts` CHECK above |
| `scheduled_for` | `timestamptz` | no | |
| `locked_at` | `timestamptz` | yes | set when a worker claims the row |
| `locked_by` | `text` | yes | worker instance identifier |
| `completed_at` | `timestamptz` | yes | `CHECK ((status IN ('SUCCEEDED','FAILED')) = (completed_at IS NOT NULL))` (migration `0005`, ADR-013) — set if and only if the row is terminal |
| `last_error` | `text` | yes | redacted before storage — same redaction pipeline as logs |
| `created_at` | `timestamptz` | no | |

## Indexes
- `INDEX (status, scheduled_for) WHERE status = 'PENDING'` — supports the
  poll query
- `UNIQUE INDEX ux_job_queue_one_live_per_type (job_type) WHERE status IN
  ('PENDING','RUNNING')` (migration `0005_job_queue_claim_constraints`,
  ADR-013) — at most one PENDING-or-RUNNING row per `job_type` at a time,
  enforced by the database so `atp_worker`'s scheduler inserts a new
  recurring job via insert-then-catch-`IntegrityError`, never
  check-then-insert (mirrors `paper.orders`'s `UNIQUE(proposal_id)` and
  `paper.trade_proposals`'s `UNIQUE(client_request_id)`)

## Access pattern
Workers claim rows via
`SELECT ... FOR UPDATE SKIP LOCKED WHERE status = 'PENDING' AND scheduled_for <= now() ORDER BY scheduled_for LIMIT 1`,
per the plan's §6 module design for `atp_worker`. Idempotent re-execution is
required: a crash mid-job must leave no partial state (tested once
`atp_worker` is implemented, in a future step - unlike `atp_exec_paper`
(Phase 1 Step 9, ADR-011), `atp_worker` holds full `SELECT`/`INSERT`/`UPDATE`
on `core.job_queue`, so `SELECT ... FOR UPDATE SKIP LOCKED` is a legitimate
claim mechanism for it, unlike for `atp_paper_exec` on `paper.trade_proposals`).

## Security boundary
Read/write: `atp_worker` only. `atp_worker` holds **no** privileges on
`paper.orders` or any execution-path table — its job types in Phase 1 never
touch order state.
