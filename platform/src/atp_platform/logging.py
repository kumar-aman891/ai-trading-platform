"""Structured JSON logging.

Pipeline order is load-bearing: the redaction processor runs last, right
before the renderer, so nothing added by an earlier processor - including
the rendered exception traceback from `format_exc_info` - can bypass it
(security/SECRET_HANDLING.md point 4).
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog
from structlog.types import EventDict, FilteringBoundLogger, WrappedLogger

from atp_platform.correlation import get_correlation_id
from atp_platform.redaction import redact_processor


def inject_correlation_id(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """structlog processor: attach the current correlation ID, if any."""
    correlation_id = get_correlation_id()
    if correlation_id is not None:
        event_dict.setdefault("correlation_id", correlation_id)
    return event_dict


def _make_service_binder(service: str) -> structlog.types.Processor:
    def _bind(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
        event_dict.setdefault("service", service)
        return event_dict

    return _bind


def _resolve_level(level: str) -> int:
    resolved = getattr(logging, level.upper(), None)
    return resolved if isinstance(resolved, int) else logging.INFO


def configure_logging(*, service: str, level: str = "INFO", stream: TextIO | None = None) -> None:
    """Configure the process-wide structlog pipeline.

    `stream` defaults to stdout; tests pass an `io.StringIO()` to capture
    and assert on rendered output.
    """
    output = stream if stream is not None else sys.stdout
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            _make_service_binder(service),
            inject_correlation_id,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(_resolve_level(level)),
        logger_factory=structlog.PrintLoggerFactory(file=output),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
