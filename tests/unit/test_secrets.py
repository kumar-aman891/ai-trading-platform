"""Tests for atp_platform.secrets — SecretProvider port + EnvSecretProvider."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from atp_platform.secrets import EnvSecretProvider, SecretProvider


def test_env_secret_provider_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TEST_SECRET", "value-123")

    provider = EnvSecretProvider()

    assert provider.get("MY_TEST_SECRET") == "value-123"


def test_env_secret_provider_returns_none_for_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEFINITELY_NOT_SET", raising=False)

    provider = EnvSecretProvider()

    assert provider.get("DEFINITELY_NOT_SET") is None


def test_env_secret_provider_satisfies_the_port_protocol() -> None:
    assert isinstance(EnvSecretProvider(), SecretProvider)


def test_secret_str_does_not_expose_value_via_repr_or_str() -> None:
    secret = SecretStr("super-secret-value")

    assert "super-secret-value" not in repr(secret)
    assert "super-secret-value" not in str(secret)
    assert secret.get_secret_value() == "super-secret-value"
