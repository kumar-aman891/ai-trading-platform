"""Safety invariant #8 (tests/safety/README.md):
`test_secret_never_appears_in_logs`.

`tests/unit/test_redaction.py` proves the redaction *function*
(`redact_mapping`/`redact_text`) strips secrets from a plain mapping. That
is necessary but not sufficient: it does not prove a secret can never reach
a *rendered log line* through `atp_platform.logging`'s actual structlog
pipeline, where `redact_processor` must run last - immediately before
`JSONRenderer` - so that nothing an earlier processor adds (in particular
the rendered exception traceback from `format_exc_info`) can bypass it
(see `atp_platform/logging.py`'s module docstring).

This test lives here, marked `@pytest.mark.safety`, rather than in
`tests/unit/test_logging.py`, specifically so it can never be silently
skipped, xfailed, or deleted without an ADR - the property it proves
(secrets never rendered) is a security invariant, not an implementation
detail of the logging module.

Must never be skipped, xfailed, or removed without an ADR.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest
import structlog
from pydantic import SecretStr

from atp_platform import correlation
from atp_platform.logging import configure_logging, get_logger

pytestmark = pytest.mark.safety


@pytest.fixture(autouse=True)
def _reset_structlog_after_each_test() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def _configured_stream() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(service="safety-test", stream=stream)
    return stream


def test_secret_in_a_bound_kwarg_never_appears_in_rendered_output() -> None:
    secret_value = "a" * 40
    stream = _configured_stream()
    logger = get_logger("safety")

    logger.info("db_connected", password=secret_value)

    assert secret_value not in stream.getvalue()


def test_secret_in_a_nested_mapping_never_appears_in_rendered_output() -> None:
    secret_value = "b" * 40
    stream = _configured_stream()
    logger = get_logger("safety")

    logger.info(
        "request_completed",
        context={"outer": {"credentials": {"api_key": secret_value}, "status": "ok"}},
    )

    assert secret_value not in stream.getvalue()
    record: dict[str, object] = json.loads(stream.getvalue().splitlines()[-1])
    context = record["context"]
    assert isinstance(context, dict)
    assert context["outer"]["credentials"]["api_key"] == "***REDACTED***"
    assert context["outer"]["status"] == "ok"


def test_secret_in_a_rendered_exception_traceback_never_appears_in_output() -> None:
    """The load-bearing case: `format_exc_info` renders the full traceback
    - including the exception's string representation - into the event
    dict *before* `redact_processor` runs. If the pipeline ordering in
    `atp_platform.logging.configure_logging` were ever changed so redaction
    ran earlier than the exception renderer, this is the test that would
    catch it."""
    secret_value = "c" * 40
    stream = _configured_stream()
    logger = get_logger("safety")

    try:
        raise ValueError(f"authentication failed using key {secret_value}")
    except ValueError:
        logger.error("operation failed", exc_info=True)

    output = stream.getvalue()
    assert secret_value not in output
    assert "ValueError" in output  # the exception itself stays informative


def test_secretstr_repr_never_leaks_the_underlying_value() -> None:
    """A `pydantic.SecretStr` masks itself on `str()`/`repr()` by
    construction; this proves that guarantee survives the full logging
    pipeline (JSON rendering, redaction) rather than assuming it does."""
    secret_value = "d" * 40
    secret = SecretStr(secret_value)
    stream = _configured_stream()
    logger = get_logger("safety")

    logger.info("settings_loaded", note=repr(secret))

    output = stream.getvalue()
    assert secret_value not in output
    assert secret.get_secret_value() == secret_value  # sanity: we held the real value


def test_correlation_id_is_not_treated_as_a_secret() -> None:
    """Redaction must not be so aggressive it destroys observability -
    UUID-shaped identifiers this platform deliberately logs (correlation
    IDs, event IDs, proposal IDs) must survive rendering unredacted."""
    stream = _configured_stream()
    logger = get_logger("safety")

    with correlation.correlation_scope("3fa85f64-5717-4562-b3fc-2c963f66afa6") as cid:
        logger.info("inside scope")

    record: dict[str, object] = json.loads(stream.getvalue().splitlines()[-1])
    assert record["correlation_id"] == cid
