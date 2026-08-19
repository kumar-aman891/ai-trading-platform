"""Unit tests for `atp_worker.errors` (Phase 1 Step 12 Phase B, ADR-013 §3).

Each error type exists to select one of ADR-013's three failure routes, so
what matters is the hierarchy (everything catchable as `WorkerError`) and
`HandlerFailedError.retryable`'s default - a handler that raises without
saying otherwise must get the retry path, not silent termination.
"""

from __future__ import annotations

from atp_worker.errors import (
    HandlerFailedError,
    LeaseExpiredError,
    NoHandlerRegisteredError,
    WorkerError,
)


def test_every_worker_error_is_catchable_as_worker_error() -> None:
    for exc in (
        HandlerFailedError("boom"),
        NoHandlerRegisteredError("unknown"),
        LeaseExpiredError("expired"),
    ):
        assert isinstance(exc, WorkerError)


def test_handler_failure_defaults_to_retryable() -> None:
    """A handler that just raises `HandlerFailedError("...")` must get the
    normal attempts/backoff path - terminating early by default would
    silently discard retries the job was configured for."""
    assert HandlerFailedError("boom").retryable is True


def test_handler_failure_can_opt_out_of_retrying() -> None:
    assert HandlerFailedError("unfixable", retryable=False).retryable is False


def test_error_messages_are_preserved() -> None:
    assert str(HandlerFailedError("a specific reason")) == "a specific reason"
