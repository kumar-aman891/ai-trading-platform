"""Typed, fail-fast configuration.

Reads from process environment variables only (no `.env` file parsing here —
loading `.env` into the process environment is a deployment concern, e.g.
docker-compose's `env_file:` directive, introduced in Phase 1 Step 5, not a
Settings responsibility).

Two independent startup gates, both enforced before or during construction,
per the approved Phase 1 plan and security/SECRET_HANDLING.md:

1. `assert_no_forbidden_credentials` — refuses to proceed if any KITE_*/LLM_*
   environment variable is present, regardless of whether Settings declares
   a field for it. Phase 1 has no legitimate use for either (ADR-006).
2. `Settings`'s own field validation — TRADING_MODE must be PAPER (LIVE is
   rejected with an explicit "not implemented" message, not a generic
   validation error, per ADR-005/ADR-008), and SESSION_SECRET_KEY must be a
   real, sufficiently long, non-placeholder secret.

`Settings` declares no field for any broker or LLM credential — not merely
an unused one. Structurally absent, per the plan's §8 "type-level least
privilege": a service holding this Settings type cannot be handed a broker
credential even by misconfiguration, because there is nowhere on the type to
put it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ConfigurationError(RuntimeError):
    """Base class for Phase 1 startup configuration failures."""


class ForbiddenCredentialError(ConfigurationError):
    """A credential Phase 1 must never hold was found in the environment."""


# ---------------------------------------------------------------------------
# Forbidden-credential scan — independent of Settings' declared fields, so
# presence alone fails startup even though Settings has no field to read
# these into.
# ---------------------------------------------------------------------------

FORBIDDEN_ENV_PREFIXES: tuple[str, ...] = ("KITE_", "LLM_")


def assert_no_forbidden_credentials(environ: Mapping[str, str] | None = None) -> None:
    """Raise ForbiddenCredentialError if any KITE_* or LLM_* variable is
    present in the given environment mapping (defaults to os.environ)."""
    env = environ if environ is not None else os.environ
    found = sorted(
        key
        for key in env
        if any(key.upper().startswith(prefix) for prefix in FORBIDDEN_ENV_PREFIXES)
    )
    if found:
        raise ForbiddenCredentialError(
            "Phase 1 forbids broker and LLM credentials in the environment "
            f"(ADR-006, security/SECRET_HANDLING.md). Found: {', '.join(found)}."
        )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_SUPPORTED_TRADING_MODES: tuple[str, ...] = ("PAPER",)

# The exact placeholder from .env.example — a config carrying this literal
# value has not actually been configured yet.
_PLACEHOLDER_SESSION_SECRET_KEY = "replace-with-a-generated-32-byte-minimum-secret"

_MIN_SESSION_SECRET_KEY_BYTES = 32


class Settings(BaseSettings):
    """Cross-cutting Phase 1 configuration.

    Immutable after construction (`frozen=True`) — there is no code path
    anywhere in this codebase that mutates a live Settings instance; a
    changed configuration is a new process, not a mutated one.
    """

    model_config = SettingsConfigDict(
        frozen=True,
        extra="ignore",
        env_file=None,
        validate_default=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    trading_mode: str = "PAPER"
    session_secret_key: SecretStr
    session_ttl_seconds: int = Field(default=28800, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json"] = "json"
    database_url: SecretStr
    redis_url: SecretStr

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not (raw.startswith("postgresql://") or raw.startswith("postgresql+psycopg://")):
            raise ValueError(
                "DATABASE_URL must be a postgresql:// or postgresql+psycopg:// DSN. "
                "Each service (api, paper-exec, worker, migrate) is given its own "
                "DSN with its own least-privilege role - see ops/sql/."
            )
        return value

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw.startswith("redis://"):
            raise ValueError("REDIS_URL must be a redis:// DSN.")
        if "@" not in raw.split("://", 1)[1].split("/", 1)[0]:
            raise ValueError(
                "REDIS_URL must include credentials (redis://:password@host:port/db) - "
                "Redis requires authentication in every Phase 1 environment (Step 5)."
            )
        return value

    @field_validator("trading_mode")
    @classmethod
    def _validate_trading_mode(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized == "LIVE":
            raise ValueError(
                "TRADING_MODE=LIVE is not implemented in this build. Phase 1 "
                "supports PAPER only - no LIVE execution path exists "
                "(see docs/adr/ADR-005-paper-live-isolation.md and "
                "docs/adr/ADR-008-order-intent-minting.md)."
            )
        if normalized not in _SUPPORTED_TRADING_MODES:
            raise ValueError(
                f"Unsupported TRADING_MODE={value!r}. Phase 1 supports: "
                f"{', '.join(_SUPPORTED_TRADING_MODES)}."
            )
        return normalized

    @field_validator("session_secret_key")
    @classmethod
    def _validate_session_secret_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw == _PLACEHOLDER_SESSION_SECRET_KEY:
            raise ValueError(
                "SESSION_SECRET_KEY is still the placeholder value from "
                ".env.example. Generate a real one: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if len(raw.encode("utf-8")) < _MIN_SESSION_SECRET_KEY_BYTES:
            raise ValueError(
                f"SESSION_SECRET_KEY must be at least {_MIN_SESSION_SECRET_KEY_BYTES} bytes."
            )
        return value


def load_settings() -> Settings:
    """The single entry point services should use to obtain Settings.

    Runs the forbidden-credential scan first (a check Settings' own field
    validation cannot express, since Settings deliberately has no field for
    these credentials) and only then constructs Settings, whose own
    validators enforce TRADING_MODE and SESSION_SECRET_KEY.
    """
    assert_no_forbidden_credentials()
    return Settings()
