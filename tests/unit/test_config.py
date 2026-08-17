"""Tests for atp_platform.config — the Phase 1 startup gates.

Every test sets its own environment via monkeypatch (auto-reverted by
pytest), so tests never depend on ambient shell state and never leak state
into each other.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from atp_platform.config import (
    ForbiddenCredentialError,
    Settings,
    assert_no_forbidden_credentials,
    load_settings,
)

VALID_SECRET = "a" * 40
VALID_DATABASE_URL = "postgresql+psycopg://atp_owner:fixture-only@localhost:5432/atp"
VALID_REDIS_URL = "redis://:fixture-only@localhost:6379/0"


@pytest.fixture(autouse=True)
def _valid_dsn_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL/REDIS_URL are required fields as of Step 5 - every test
    in this module gets a valid default so it can focus on the one thing
    it's actually testing; tests exercising DSN validation itself override
    these explicitly."""
    monkeypatch.setenv("DATABASE_URL", VALID_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", VALID_REDIS_URL)


def test_valid_paper_configuration_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)

    settings = load_settings()

    assert settings.trading_mode == "PAPER"
    assert settings.environment == "development"


def test_live_trading_mode_is_rejected_with_explicit_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)

    with pytest.raises(ValidationError, match="not implemented in this build"):
        load_settings()


def test_unsupported_trading_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "SANDBOX")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)

    with pytest.raises(ValidationError, match="Unsupported TRADING_MODE"):
        load_settings()


def test_kite_credential_presence_rejects_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)
    monkeypatch.setenv("KITE_API_KEY", "whatever-it-does-not-matter")

    with pytest.raises(ForbiddenCredentialError, match="KITE_API_KEY"):
        load_settings()


def test_llm_credential_presence_rejects_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)
    monkeypatch.setenv("LLM_API_KEY", "whatever-it-does-not-matter")

    with pytest.raises(ForbiddenCredentialError, match="LLM_API_KEY"):
        load_settings()


def test_database_url_requires_a_postgresql_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)
    monkeypatch.setenv("DATABASE_URL", "mysql://atp_owner:fixture-only@localhost:3306/atp")

    with pytest.raises(ValidationError, match="postgresql"):
        load_settings()


def test_redis_url_requires_a_redis_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)
    monkeypatch.setenv("REDIS_URL", "memcached://localhost:11211")

    with pytest.raises(ValidationError, match="redis://"):
        load_settings()


def test_redis_url_without_credentials_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis must require authentication in every Phase 1 environment
    (Step 5) - a DSN with no credentials segment is rejected at startup
    rather than silently connecting unauthenticated."""
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with pytest.raises(ValidationError, match="authentication"):
        load_settings()


def test_forbidden_credential_scan_is_independent_of_settings_fields() -> None:
    """The scan operates on raw environment keys, not declared Settings
    fields — this is what makes it catch KITE_*/LLM_* even though Settings
    never declares a field for them."""
    assert_no_forbidden_credentials({"UNRELATED": "x"})  # does not raise

    with pytest.raises(ForbiddenCredentialError, match="KITE_ACCESS_TOKEN"):
        assert_no_forbidden_credentials({"KITE_ACCESS_TOKEN": "x"})


def test_session_secret_key_too_short_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", "too-short")

    with pytest.raises(ValidationError, match="at least 32 bytes"):
        load_settings()


def test_session_secret_key_placeholder_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", "replace-with-a-generated-32-byte-minimum-secret")

    with pytest.raises(ValidationError, match="placeholder"):
        load_settings()


def test_settings_is_immutable_after_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", VALID_SECRET)
    settings = load_settings()

    with pytest.raises(ValidationError):
        settings.trading_mode = "PAPER"  # type: ignore[misc]


def test_settings_has_no_broker_or_llm_credential_field() -> None:
    """Structural check: no field exists to hold a broker/LLM credential,
    not merely that one is unused (ADR-006)."""
    for name in Settings.model_fields:
        lowered = name.lower()
        assert "kite" not in lowered
        assert "broker" not in lowered
        assert "llm" not in lowered


def test_secret_str_field_not_exposed_via_repr_or_str(monkeypatch: pytest.MonkeyPatch) -> None:
    secret_value = "correct-horse-battery-staple-0123456789"  # gitleaks:allow - synthetic test fixture, not a real credential
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setenv("SESSION_SECRET_KEY", secret_value)

    settings = load_settings()

    assert secret_value not in repr(settings)
    assert secret_value not in str(settings)
    assert settings.session_secret_key.get_secret_value() == secret_value
