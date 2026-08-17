"""Durable job-table worker.

Empty in Phase 1 Step 2. Populated in Phase 1 Step 16. A durable job table
with SELECT ... FOR UPDATE SKIP LOCKED, per docs/TECH_STACK.md — no Celery,
no message broker.
"""
