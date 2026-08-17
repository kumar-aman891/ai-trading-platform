"""Correlation ID propagation via contextvars.

One correlation ID flows from an inbound request (or a job/task boundary)
through every log record and, eventually, into the audit trail — see
docs/OBSERVABILITY.md. This module owns the single source of truth for the
current correlation ID; atp_platform.logging reads it via
`inject_correlation_id`, and atp_platform.asgi's middleware sets it per
HTTP request.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    """The correlation ID bound to the current context, if any."""
    return _correlation_id_var.get()


def set_correlation_id(correlation_id: str) -> Token[str | None]:
    """Bind a correlation ID to the current context. Returns a reset token."""
    return _correlation_id_var.set(correlation_id)


def reset_correlation_id(token: Token[str | None]) -> None:
    """Undo a prior `set_correlation_id` call using its token."""
    _correlation_id_var.reset(token)


def new_correlation_id() -> str:
    """A freshly generated correlation ID. Not bound to any context."""
    return str(uuid.uuid4())


@contextmanager
def correlation_scope(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation ID for the duration of the `with` block.

    Generates a new one if none is supplied — this is what makes "a
    correlation ID is created when absent" true for any caller (a worker
    job, a script, a test) that isn't going through the ASGI middleware.
    """
    cid = correlation_id or new_correlation_id()
    token = set_correlation_id(cid)
    try:
        yield cid
    finally:
        reset_correlation_id(token)
