"""Durable job-table worker (ADR-013 "Operational Worker Scope").

A background, self-scheduled process with no HTTP route and no enqueue
path from any other service (`scheduler.py`'s `ensure_recurring_jobs_
scheduled` is the sole producer into `core.job_queue`). Claims rows via
`SELECT ... FOR UPDATE SKIP LOCKED`, per docs/TECH_STACK.md — no Celery,
no message broker. Executes exactly three job types - `SESSION_REAP`
(observation only), `AUDIT_INTEGRITY_CHECK` (window attestation, not hash
chaining), `RETENTION` (prunes `core.job_queue`'s own terminal rows) -
and is never an order execution path: it holds zero grants on any
`paper`/`live` table (safety invariant #17,
`tests/safety/test_no_execution_path_in_worker.py`).
"""
