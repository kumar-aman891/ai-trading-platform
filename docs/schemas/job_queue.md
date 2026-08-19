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

## Access pattern (ADR-013 "Operational Worker Scope", §3-§6)

`scheduler.py`'s `ensure_recurring_jobs_scheduled()` is the **only**
producer of rows - one PENDING-or-RUNNING row per `job_type` at a time,
enforced by `ux_job_queue_one_live_per_type` (insert, catch
`IntegrityError`, never check-then-insert). `atp_worker` holds full
`SELECT`/`INSERT`/`UPDATE`/`DELETE` on this table (unlike `atp_paper_exec`
on `paper.trade_proposals`, ADR-011), so `SELECT ... FOR UPDATE SKIP
LOCKED` is a legitimate claim mechanism here.

**Three transactions per job, never one:**

1. **Claim (Tx A).**
   `SELECT ... FOR UPDATE SKIP LOCKED WHERE status = 'PENDING' AND scheduled_for <= now() ORDER BY scheduled_for, job_id LIMIT 1`,
   then `UPDATE ... SET status='RUNNING', attempts = attempts + 1, locked_at = now(), locked_by = :instance_id`,
   committed before any handler runs. `attempts` increments **here, at
   claim**, not on failure - a process that crashes mid-handler still
   costs exactly one attempt, so a handler that reliably kills the
   process cannot loop forever uncounted.
2. **Handler + audit + terminal update (Tx B).** The handler's own work,
   its `audit.audit_events` insert where applicable (never for
   `SESSION_REAP`), and `status='SUCCEEDED', completed_at=now(),
   locked_at=NULL, locked_by=NULL` all commit together (safety invariant
   #14 - the audit write and the state change it documents are never two
   transactions).
3. **Failure (Tx C, a fresh transaction after Tx B rolls back).** Under
   `max_attempts`: back to `PENDING` with `scheduled_for = now() +
   backoff(attempts)` (`backoff(n) = min(5 * 2^(n-1), 300)` seconds, no
   jitter - Phase 1 runs a single instance). Exhausted: `FAILED` with
   `completed_at` set - terminal, alertable, never retried again. There is
   no `DEAD_LETTER` state: the claim query already excludes anything not
   `PENDING`, so `FAILED` is already unreachable by a future claim.

**Lease sweep.** At the top of every poll cycle, in its own transaction: a
`RUNNING` row whose `locked_at` predates `now() - 300s` (`LEASE_DURATION`,
an application constant, not a column) is functionally a crashed claim and
is routed through the same PENDING/FAILED decision as any other failure.

Idempotent re-execution: `RETENTION` and `SESSION_REAP` are naturally
idempotent (a pure `DELETE ... WHERE completed_at < cutoff` and a pure
`SELECT`, respectively); `AUDIT_INTEGRITY_CHECK` is *value*-idempotent, not
row-idempotent - the audit ledger it writes to is append-only, so re
-attesting the same window produces a second row that must agree with the
first on every attested value, never a mutation of the first.

## Security boundary
Read/write: `atp_worker` only. `atp_worker` holds **no** privileges on
`paper.orders` or any execution-path table, and no `USAGE` on the `paper`
or `live` schema at all — its three job types never touch order state
(safety invariant #17, `tests/safety/test_no_execution_path_in_worker.py`).
