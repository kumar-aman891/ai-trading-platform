"""Tests for atp_platform.logging — structured JSON output with redaction
applied last in the pipeline, and correlation ID injection."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest
import structlog

from atp_platform import correlation
from atp_platform.logging import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_structlog_after_each_test() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def _configured_stream(service: str = "test-service") -> io.StringIO:
    stream = io.StringIO()
    configure_logging(service=service, stream=stream)
    return stream


def _last_record(stream: io.StringIO) -> dict[str, object]:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    result: dict[str, object] = json.loads(lines[-1])
    return result


def test_log_output_is_valid_json_with_expected_fields() -> None:
    stream = _configured_stream()
    logger = get_logger("test")

    logger.info("hello")

    record = _last_record(stream)
    assert record["event"] == "hello"
    assert record["level"] == "info"
    assert record["service"] == "test-service"
    assert "timestamp" in record


def test_known_secret_never_appears_in_logs_under_a_denylisted_key() -> None:
    secret_value = "s" * 40
    stream = _configured_stream()
    logger = get_logger("test")

    logger.info("db_connected", password=secret_value)

    output = stream.getvalue()
    assert secret_value not in output
    record = _last_record(stream)
    assert record["password"] == "***REDACTED***"


def test_known_secret_never_appears_in_logs_via_pattern_match() -> None:
    """Even under an innocuous key name, a high-entropy value is caught by
    the value-pattern defense (redaction.py)."""
    secret_value = "t" * 40
    stream = _configured_stream()
    logger = get_logger("test")

    logger.info("unexpected value observed", observed=secret_value)

    assert secret_value not in stream.getvalue()


def test_known_secret_never_appears_in_exception_output() -> None:
    secret_value = "u" * 40
    stream = _configured_stream()
    logger = get_logger("test")

    try:
        raise ValueError(f"failed using key {secret_value}")
    except ValueError:
        logger.error("operation failed", exc_info=True)

    output = stream.getvalue()
    assert secret_value not in output
    assert "ValueError" in output  # the exception itself is still informative


def test_correlation_id_is_available_to_log_records() -> None:
    stream = _configured_stream()
    logger = get_logger("test")

    with correlation.correlation_scope("known-correlation-id") as cid:
        logger.info("inside scope")

    record = _last_record(stream)
    assert record["correlation_id"] == cid == "known-correlation-id"


def test_no_correlation_id_field_when_none_is_bound() -> None:
    stream = _configured_stream()
    logger = get_logger("test")

    logger.info("outside any scope")

    record = _last_record(stream)
    assert "correlation_id" not in record
