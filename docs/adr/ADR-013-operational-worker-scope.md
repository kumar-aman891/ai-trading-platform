# ADR-013: Operational Worker Scope — Self-Scheduled Jobs, Observation-Only Sessions, and Window-Attestation Audit Integrity

## Status
Accepted — Phase 1 (Step 12 Phase B), pending implementation.

## Context

`core.job_queue` (`docs/schemas/job_queue.md`) and the `atp_worker` role
(`ops/sql/roles_and_schemas.sql.tmpl`) have existed since Phase 1 Step 2,
but `workers/src/atp_worker/` has held only a docstring since then. Phase 1
Step 12 Phase A verified the data plane this worker will run against: the
integration suite passes 78/78 against a real PostgreSQL/Redis stack on
`main` at `3f3bd26`, with the integration job now a blocking CI gate.

The grants this worker will run under are unusual enough to require a
decision record before any code depends on them:

- `atp_worker` holds full `SELECT, INSERT, UPDATE, DELETE` on
  `core.job_queue` (`0003_table_grants.py:65`, unrevoked from the Step 2
  baseline at `roles_and_schemas.sql.tmpl:118`) — `SELECT ... FOR UPDATE
  SKIP LOCKED` is a legitimate claim mechanism here, unlike `atp_paper_exec`
  on `paper.trade_proposals` (ADR-011).
- `atp_worker` holds **column-scoped** `SELECT` on exactly
  `(session_id_hash, expires_at, revoked_at)` of `core.sessions`
  (`0003_table_grants.py:141`), no table-level DML. A caller that runs
  `select(SessionRow)` (the existing `SqlAlchemySessionRepository.get_by_hash`,
  `persistence/src/atp_persistence/repositories/sessions.py:51`) requests
  all seven columns and raises `InsufficientPrivilege` under this role. No
  unit test with a fake can catch this; only an integration test against
  the real role can.
- `atp_worker` holds **no USAGE on `paper` or `live`**
  (`roles_and_schemas.sql.tmpl:103`) — stronger than any import-linter
  contract could express, and this ADR does not weaken it.
- `audit.audit_events` grants `atp_worker` only `SELECT, INSERT`
  (`roles_and_schemas.sql.tmpl:131`); the append-only trigger
  (`audit_events_append_only`, ADR-010) additionally rejects UPDATE/DELETE
  for every role including `atp_owner`.

Two pieces of existing documentation describe a capability this worker will
not have, and are corrected by this ADR rather than by code:

- `docs/schemas/session.md:22,30-31` describes `atp_worker`'s
  column-scoped access as being "for the reaper job" and cites an index
  supporting "the worker's session-reaper job" — but reaping (revoking or
  deleting) requires write access this role does not and will not hold.
- `workers/pyproject.toml`'s `description` field promises "session
  reaping" and "audit-chain integrity checks" — the former is the same
  overreach as `session.md`; the latter pulls forward a design ADR-010
  explicitly defers ("hash chaining... Phase 4... there is no `prev_hash`
  column").

This ADR also settles implementation-critical behavior identified during
Step 12 Phase B reconnaissance that the existing schema/docs leave implicit,
so that migration 0005 can encode it as a constraint rather than a
convention.

## Decision

### 1. Scope

`atp_worker` is a background, self-scheduled process with **no HTTP
route**, **no `atp_api` grant on `core.job_queue`** (confirmed: `0003`
revokes `SELECT, INSERT, UPDATE, DELETE` on `core.job_queue` from `atp_api`
entirely, `:66`), and **no enqueue path from any other service**. It reads
its own job table, executes exactly three job types, and writes to
`audit.audit_events` and `core.job_queue` only. It is never an order
execution path — restating ADR-011 §3: `atp_worker` holds zero grants on
any `paper`/`live` table, so a future phase that wants worker-driven
execution must reopen this ADR rather than add an import.

### 2. The three job types — exact semantics

`core.job_queue.job_type` is `CHECK (job_type IN ('SESSION_REAP',
'AUDIT_INTEGRITY_CHECK', 'RETENTION'))` today (`models/core.py:213`) and
this ADR does not change that allowlist.

**`AUDIT_INTEGRITY_CHECK`** — window attestation, **not hash chaining**.
Payload carries `window_start`, `window_end` (both fixed at enqueue time,
read from the payload, never from wall-clock at run time — see §12). The
handler computes `(count(*), max(event_id), max(recorded_at))` over
`audit.audit_events` restricted to that closed, past window and writes it
as a new, immutable attestation row via `INSERT` (never `UPDATE`). A later
run over the *same* window recomputes the aggregate and compares it
against the attested value read back from the ledger. This detects
disappearance from, or backdating into, an already-attested window using
only the `SELECT, INSERT` grant this role already holds. It proves nothing
about tampering *within* a still-open window, and provides no
cryptographic linkage between rows — that capability remains deferred to
Phase 4 under ADR-010 and is explicitly out of scope here.

**`RETENTION`** — prunes `core.job_queue`'s **own** terminal rows only:
`DELETE WHERE status IN ('SUCCEEDED','FAILED') AND completed_at <
:cutoff`. This is job-table housekeeping, not a data-retention or
compliance policy — no such policy exists in this repository (confirmed:
exhaustive grep of `docs/`, `security/`, `.claude/`, `config/`, `planning/`
for `retention|purge|expiry|TTL` during Phase 1 Step 12 reconciliation
found none), and `atp_worker` holds `DELETE` on no table other than
`core.job_queue`. Cutoff is **7 days** (`RETENTION_WINDOW_DAYS = 7`),
read from the job's own payload if present, defaulting to 7 if absent —
chosen as a conservative default with no existing traffic pattern to size
it against; revisit once real job volume exists.

**`SESSION_REAP`** — **observation only**. Counts sessions matching
`expires_at < now() AND revoked_at IS NULL` using only the three granted
columns, and emits one structured log line plus a metric
(`atp_worker.session_reap.expired_unrevoked_count`). **No state change, no
audit event.** `core.sessions` is never written by this job type, this
process, or any future job type added under this name — a security
boundary, not an implementation gap. This corrects `session.md`'s wording
(§6). There is no security exposure this leaves open:
`validate_and_renew_session` already returns `EXPIRED` and refuses renewal
for an expired session regardless of `revoked_at`; stamping `revoked_at`
for natural expiry would overload a field `session.md` defines as "logout
or administrative revocation," corrupting `ACTION_SESSION_REVOKED`
semantics for every consumer of that audit action. `atp_api` is untouched
by this decision.

A detected `AUDIT_INTEGRITY_CHECK` violation is a **successful check**,
recorded as `status='SUCCEEDED'` plus an
`ACTION_AUDIT_INTEGRITY_VIOLATION_DETECTED` event — never a job failure.
Conflating detection with failure would retry a genuine tamper signal up
to `max_attempts` times and bury it in `last_error` instead of surfacing it
once, clearly, in the audit ledger.

### 3. Claim transaction vs. handler transaction — three transactions, never one

**Tx A (claim, short, its own transaction):**
```sql
SELECT ... FROM core.job_queue
WHERE status = 'PENDING' AND scheduled_for <= now()
ORDER BY scheduled_for, job_id
FOR UPDATE SKIP LOCKED LIMIT 1
```
followed by `UPDATE ... SET status='RUNNING', attempts = attempts + 1,
locked_at = :now, locked_by = :instance_id`, committed before the handler
runs. `ORDER BY scheduled_for, job_id` — the `job_id` tiebreaker (UUIDv7,
so it is also roughly time-ordered) makes claim order deterministic for
tests without adding a column.

`attempts` increments **at claim, not at failure**. This is deliberate:
it is what makes a hard process crash mid-handler cost exactly one
attempt against `max_attempts`, rather than the crash going uncounted and
a poison job (one whose handler reliably kills the process) looping
forever. The rejected alternative — claiming inside the handler's own
transaction — was considered and rejected for exactly this reason: a crash
there rolls back the attempt increment along with everything else.

**Tx B (handler + terminal update + audit, one transaction):** the
handler's work, its `audit.audit_events` insert (where applicable — never
for `SESSION_REAP`, see §2), and the terminal
`status='SUCCEEDED', completed_at=:now, locked_at=NULL, locked_by=NULL`
commit together. This is the worker's instance of safety invariant #14
(`test_audit_event_and_state_change_share_a_transaction`,
`tests/safety/README.md`) — the same one-transaction discipline
`PaperExecutionUnitOfWork` already enforces for `atp_exec_paper`.

**Tx C (failure, a fresh transaction after B rolled back):** if
`attempts < max_attempts`, the job returns to `status='PENDING'` with
`scheduled_for = now() + backoff(attempts)`; otherwise it moves to
`status='FAILED'` with `completed_at` set. Retryable failures never sit in
`FAILED` — `FAILED` means exhausted, terminal, and alertable, with no
`DEAD_LETTER` state (rejected in the original Phase B reconnaissance: the
poll query already selects only `status='PENDING'`, so `FAILED` is already
excluded from re-claim and a separate state adds nothing).

An **unknown `job_type`** (should be unreachable given the CHECK
constraint and the registry-parity safety test, §13, but handled
defensively) fails immediately to `FAILED` with no retry — retrying
something nothing can execute is noise, not resilience.

### 4. Backoff

Exponential with a fixed base and cap, no jitter (single-instance worker
in Phase 1; jitter exists to desynchronize competing instances, which does
not yet apply):

```
backoff(attempts) = min(BACKOFF_BASE_SECONDS * 2 ** (attempts - 1), BACKOFF_CAP_SECONDS)
BACKOFF_BASE_SECONDS = 5
BACKOFF_CAP_SECONDS  = 300
```

With `max_attempts` default `3` (the column's existing `server_default`,
`models/core.py:236`), a job retries at approximately +5s and +10s before
exhausting to `FAILED`.

### 5. Lease semantics and duration

A **lease sweep** runs at the top of each poll cycle, in its own
transaction, `SKIP LOCKED` in the subquery so concurrent sweepers (should
a second instance ever run) never block each other:

```sql
SELECT job_id FROM core.job_queue
WHERE status = 'RUNNING' AND locked_at < now() - :lease_duration
FOR UPDATE SKIP LOCKED
```

Rows found are reclaimed to `PENDING` (if `attempts < max_attempts`) or
`FAILED` (if exhausted) — the same Tx C failure path as §3, since a lease
expiry is functionally a crash. `LEASE_DURATION = 300 seconds (5
minutes)`, an application constant in `runner.py`, not a stored column.
This resolves the previously-open question of a value: 5 minutes is
chosen as several multiples of the longest handler in this ADR's scope
(`AUDIT_INTEGRITY_CHECK`'s single-window aggregate query, expected to run
in low seconds against Phase 1 data volumes) while staying short enough
that a genuinely crashed worker doesn't leave a job stuck for an
operationally awkward length of time. A per-job lease duration is
explicitly deferred (§9) — nothing in Phase 1 needs one, and adding it
now would be a stored column with no consumer.

A lease shorter than an actual handler run causes double execution (the
sweep reclaims a still-running job). This is survivable by design:
`RETENTION` and `SESSION_REAP` are idempotent by construction (§7), and
`AUDIT_INTEGRITY_CHECK` is value- not row-idempotent, so a genuine
double-run under a too-short lease produces two attestation rows for the
same window rather than corrupting state — an operational anomaly to
notice and tune the lease for, not a correctness failure.

### 6. Scheduler poll interval

`atp_worker` mirrors `atp_exec_paper`'s existing constant-and-env-override
pattern exactly (`atp_exec_paper.gateway.DEFAULT_POLL_INTERVAL_SECONDS =
2.0`, overridden by `ATP_PAPER_EXEC_POLL_INTERVAL_SECONDS` in
`__main__.py`):

```
DEFAULT_POLL_INTERVAL_SECONDS = 5.0   # atp_worker.runner
```
overridable via `ATP_WORKER_POLL_INTERVAL_SECONDS`. Sleep only fires when
a poll cycle claims nothing (mirrors `gateway.py`'s existing
sleep-only-when-batch-empty behavior) — this worker has no batch-size
concept to mirror alongside it, since Phase 1 defines no job type that
benefits from claiming more than one row per cycle.

`scheduler.py`'s `ensure_recurring_jobs_scheduled()` is the **only**
producer of rows into `core.job_queue` (§1) — it upserts one pending row
per job type if none is currently `PENDING` or `RUNNING` for that type,
enforced at the database level by migration 0005's
`ux_job_queue_one_live_per_type` partial unique index (insert, catch
`IntegrityError`, return — never check-then-insert, matching the
`UNIQUE(proposal_id)` / `UNIQUE(client_request_id)` precedent from Steps 9
and 10). Recurrence cadence per type:

- `AUDIT_INTEGRITY_CHECK`: every 15 minutes, `window_start`/`window_end`
  computed as the prior, now-closed 15-minute boundary — chosen so every
  window is attested exactly once shortly after it closes, with no gap
  and no overlap.
- `RETENTION`: once per day.
- `SESSION_REAP`: every 5 minutes — frequent enough that the emitted
  metric is useful for alerting, cheap enough (a single indexed count
  query) that this is not a meaningful load concern.

These three cadences are application constants in `scheduler.py`, not
configuration, matching this repository's existing preference for typed
constants over a scheduling DSL (rejected explicitly, both in the original
reconnaissance and here: "no cron/config scheduling DSL" is out of scope
for Phase 1).

### 7. Idempotency

- `AUDIT_INTEGRITY_CHECK` is **value-idempotent, not row-idempotent**: the
  ledger is append-only, so a second run over the same window necessarily
  writes a second row and cannot do otherwise. Two such rows are expected
  to agree on every field except `event_id`/`recorded_at`. This is
  provable — window bounds come from the payload, fixed at enqueue, never
  from run-time clock state.
- `RETENTION` is genuinely idempotent: cutoff derives from the payload (or
  the default), so a second run deletes zero rows once the first has run,
  and the surviving row set is identical either way. It must never delete
  a `PENDING` or `RUNNING` row, including its own row while it executes
  (§2).
- `SESSION_REAP` is trivially idempotent — a pure `SELECT`.

### 8. `last_error` redaction and truncation

`last_error` is populated only on the Tx C failure path (§3), via
`atp_platform.redaction.redact_text` (the existing, general-purpose
redaction pipeline used by logs — no worker-specific redaction path is
introduced), then **truncated to 2000 characters**. The value stored is
the exception class name plus the redacted message — **never a
traceback**, matching `docs/schemas/job_queue.md`'s existing requirement
("redacted before storage — same redaction pipeline as logs"). The 2000
character figure did not previously exist in `redaction.py` or
`job_queue.md`; it is fixed here as an application-level truncation
applied by `atp_worker` after redaction, not a change to
`atp_platform.redaction`'s public API, chosen to keep a single
pathological error message from dominating the table without needing a
schema-level column-length constraint (the column remains an untruncated
`text` type).

### 9. `source_refs` encoding

`atp_domain.audit.AuditEvent.source_refs` is typed `Mapping[str, str]`
(`domain/src/atp_domain/audit.py:48`) and this ADR does not change that
type. `AUDIT_INTEGRITY_CHECK`'s non-string observed values —
`observed_count` (int), `max_event_id` (uuid) — are encoded as their
`str()` representation before being placed in `source_refs`, exactly as
every other numeric/UUID value already flowing through `source_refs`
elsewhere in this codebase is stringified at the call site (there is no
precedent anywhere in `atp_domain.audit` for a non-string value in this
mapping; this ADR does not create one). Keys:
`{job_id, job_type, window_start, window_end, observed_count,
max_event_id}`, all `str`.

### 10. Worker DSN and settings wiring

Verified against `platform/src/atp_platform/config.py:107` and
`execution/paper/src/atp_exec_paper/__main__.py:5-8`: `Settings` carries a
single, service-agnostic `database_url: SecretStr` field populated from
the `DATABASE_URL` environment variable — there is no per-service DSN
field in `Settings`, and none is added by this ADR. Each service process
is handed a *different value* of that same variable in its own
environment (its own role's connection string), exactly as
`atp_exec_paper/__main__.py`'s docstring states for that process:
"expected to be given the `atp_paper_exec` role's DSN in its own
environment, never `atp_owner`'s or `atp_api`'s." `atp_worker/__main__.py`
follows the identical pattern: it is expected to be given the
`atp_worker` role's DSN (`ops/sql/roles_and_schemas.sql.tmpl`'s
`ATP_WORKER_PASSWORD`-derived connection string) via `DATABASE_URL` in its
own process environment, read through `atp_platform.config.load_settings`
exactly as `atp_exec_paper` does. No new settings field, no new
environment variable naming scheme, is introduced.

### 11. Handler registry ↔ DB `job_type` allowlist

`registry.py`'s `HANDLER_REGISTRY: Mapping[str, JobHandler]` must contain
exactly the three keys `{SESSION_REAP, AUDIT_INTEGRITY_CHECK, RETENTION}`
— the same three, and only those three, enumerated in
`JobQueueRow.__table_args__`'s `valid_job_type` CHECK constraint. This
equality is asserted **bidirectionally** by the safety test in §13,
parsed out of the constraint's SQL text rather than duplicated as a
literal in the test, so the database and the code cannot silently drift
apart in either direction. This is also why all three handlers ship
together in this ADR's scope rather than incrementally: dropping any one
of them would weaken that assertion to a subset check, permanently.

### 12. Time source

Every use of "now" in this ADR — claim eligibility, lease expiry, backoff
scheduling, retention cutoff — is read from the injected `Clock`
(`atp_domain.clock`), the same dependency-injected clock every other
Phase 1 service already uses, never from a bare `datetime.now()` call
inside `runner.py` or any handler. This is what makes `run_poll_loop`
deterministically testable with a `FrozenClock`, matching
`tests/unit/exec_paper/`'s existing convention.

### 13. Safety boundary

One new safety test, `tests/safety/test_no_execution_path_in_worker.py`,
following the AST pattern of
`tests/safety/test_no_execution_path_in_atp_exec_paper.py`:

1. No module under `atp_worker` imports `atp_exec_paper`, `atp_api`,
   `atp_domain.intents`, `atp_domain.risk.engine`, or
   `atp_persistence.models.paper`.
2. No public function in `atp_worker` accepts a parameter named for a
   symbol, quantity, price, side, or order-type field.
3. `set(HANDLER_REGISTRY)` exactly equals the `job_type` CHECK allowlist
   (§11), parsed from `JobQueueRow.__table_args__`.

`tests/safety/README.md`'s closing line ("Every row in this table is now
✅ implemented") is updated to add row #17 for this invariant, noting
explicitly that #17 is a repo-added invariant beyond the original
16-item approved plan, matching how #14/#15/#16 were each added and noted
in turn.

### 14. Explicit non-scope

No HTTP route. No `atp_api` change of any kind, including any form of
lazy session revocation. No grant widening on any table for any role. No
deletion from `core.sessions` by any process. No hash chaining (remains
Phase 4, ADR-010). No cron/config scheduling DSL. No job priorities,
dependencies, or fan-out. No multi-instance leader election. No Redis
involvement in job claiming (Redis remains session-store only, per
existing architecture). No `dedupe_key`, `lease_expires_at`, or
`DEAD_LETTER` column — every Phase 1 job type is a recurring singleton
enforced by the partial unique index (§6), lease duration is a single
uninherited constant (§5), and `FAILED` is already terminal (§3). No
`paper`/`live` grant or access of any kind. No broker/Kite/MCP, LLM,
market-data, or egress capability. No frontend change.

### 15. Deferred, named

Parameterized job types and a real `dedupe_key` (first needed when a
per-strategy job type is added, not before). Per-job lease durations
(needed only once lease requirements diverge across job types — none do
in Phase 1). A real data-retention/compliance policy and its owner (does
not exist anywhere in this repository today; `RETENTION`'s job-table
housekeeping is not a substitute for one). Hash chaining for audit
integrity (Phase 4, ADR-010, needs a `prev_hash` column this ADR does not
add). Physical session deletion. Worker containerization and Docker
hardening (blocked on runnable app containers not existing yet —
`docker-compose.yml` defines only `postgres` and `redis`).

## Consequences

- Migration 0005 can now encode `ux_job_queue_one_live_per_type`,
  `terminal_state_has_completed_at`, and `attempts_within_bounds` as
  constraints with a settled semantic behind each, rather than a
  convention that later code might violate.
- `docs/schemas/session.md:22,30-31` needs a wording correction (drop
  "reaper job" language, keep the index — it still supports the
  observation query) as a documentation follow-up to this ADR, not a
  behavior change.
- `workers/pyproject.toml`'s `description` field needs correcting to drop
  "session reaping" and "audit-chain integrity" as a documentation
  follow-up — both overstate what this ADR authorizes.
- `workers/src/atp_worker/__init__.py`'s stale "Phase 1 Step 16" reference
  needs correcting as part of implementing this ADR, not before.
- Every implementation-critical value this ADR fixes (lease duration,
  backoff, poll interval, retention cutoff, cadences, truncation length)
  is a named constant in `runner.py`/`scheduler.py`, not a schema column
  or a configuration surface — consistent with §14/§15's rejection of a
  scheduling DSL and per-job configuration for Phase 1.

## Ambiguity not resolved by this ADR

None identified as implementation-blocking. Two items are deliberately
left as tunable constants rather than researched values, because no
production traffic exists yet to size them against: `RETENTION_WINDOW_DAYS
= 7` (§2) and `LEASE_DURATION = 300s` (§5). Both are named, both are
overridable only by changing the constant (not by environment variable, to
keep them auditable in code review rather than silently reconfigurable per
deployment) — revisit once real job-queue volume and handler runtimes are
observed.
