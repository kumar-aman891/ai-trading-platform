"""Exception types for the `atp_worker` runtime (ADR-013).

Deliberately small. ADR-013 §3 defines exactly three routes a job can take
out of a failed execution, and each error type below exists to select one
of them - none exists to carry extra state a handler might one day want:

- `HandlerFailedError(retryable=True)` -> Tx C's retry branch: back to
  `PENDING` with backoff, or `FAILED` once `attempts` reaches
  `max_attempts`.
- `HandlerFailedError(retryable=False)` -> Tx C's terminal branch
  immediately, skipping the remaining attempts. For a failure a handler
  already knows retrying cannot fix.
- `NoHandlerRegisteredError` -> immediate `FAILED`, no retry (ADR-013 §3:
  "retrying something nothing can execute is noise, not resilience").

`LeaseExpiredError` is never raised by a handler - `atp_worker.runner`'s
lease sweep constructs one purely so a reclaimed job's `last_error` reads
the same way a normal failure's does, through the same
`format_last_error` path.
"""

from __future__ import annotations


class WorkerError(Exception):
    """Base for every error this package raises. Never raised directly."""


class HandlerFailedError(WorkerError):
    """A job handler failed.

    `retryable` selects which Tx C branch `runner` takes: `True` (the
    default) routes through the normal attempts/backoff path, `False`
    fails the job terminally on this attempt regardless of how many
    attempts remain. A handler that simply raises an unexpected
    exception - anything that is not a `WorkerError` - is treated as
    `retryable=True`, since `runner` cannot know otherwise.
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class NoHandlerRegisteredError(WorkerError):
    """No handler is registered for a claimed job's `job_type`.

    Should be unreachable in a correctly deployed system: `core.job_queue`'s
    `valid_job_type` CHECK constraint bounds what can be stored, and
    safety invariant #17 (a later step) asserts `HANDLER_REGISTRY` covers
    exactly that allowlist. Handled defensively anyway - a claimed job
    that nothing can execute must reach a terminal state rather than
    cycle."""


class LeaseExpiredError(WorkerError):
    """A `RUNNING` job's lease elapsed before it reported a terminal state
    (ADR-013 §5) - functionally a crashed claim, routed through the same
    Tx C failure path as any other failure."""
