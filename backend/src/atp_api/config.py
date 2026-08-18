"""API-layer configuration - CORS and rate-limit settings that are specific
to `atp_api` and have no reason to be visible to `atp_exec_paper`/
`atp_worker` (which is why these live here rather than being added to the
shared `atp_platform.config.Settings`, per that module's "each service's
Settings class only declares the fields it needs" principle).

`atp_platform.config.Settings` remains the source of truth for
`trading_mode`, `database_url`, `session_secret_key`, and `environment` -
`ApiSettings` is composed alongside it, never a replacement for it.
"""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Immutable, fail-fast, like `atp_platform.config.Settings`."""

    model_config = SettingsConfigDict(
        frozen=True,
        extra="ignore",
        env_file=None,
        validate_default=True,
    )

    # Comma-separated in the environment (CORS_ALLOWED_ORIGINS=https://a,https://b).
    # Empty by default - CORS is allow-list based and opt-in, never a
    # wildcard default (docs/SECURITY.md: "strict CORS").
    cors_allowed_origins: tuple[str, ...] = Field(default_factory=tuple)
    cors_allow_credentials: bool = False

    rate_limit_requests: int = Field(default=100, gt=0)
    rate_limit_window_seconds: float = Field(default=60.0, gt=0)

    # Step 8: a stricter, dedicated limit for login attempts
    # (docs/SECURITY.md/rules/02-live-trading.md's "order-rate limit"
    # pattern applied to authentication) - independent of the general
    # per-path `rate_limit_requests` above.
    login_rate_limit_requests: int = Field(default=5, gt=0)
    login_rate_limit_window_seconds: float = Field(default=60.0, gt=0)

    # Step 8: the one-time secret gating the bootstrap-admin process
    # (`atp_api.bootstrap`). `None` means bootstrap is disabled - there is
    # no placeholder value that would accidentally enable it, unlike
    # `Settings.session_secret_key`'s placeholder-rejection pattern, because
    # the unset case here is itself the safe default.
    bootstrap_admin_token: SecretStr | None = None

    @staticmethod
    def _split_csv(value: object) -> object:
        if isinstance(value, str):
            return tuple(origin.strip() for origin in value.split(",") if origin.strip())
        return value

    @model_validator(mode="before")
    @classmethod
    def _parse_csv_origins(cls, data: object) -> object:
        if isinstance(data, dict) and "cors_allowed_origins" in data:
            data = dict(data)
            data["cors_allowed_origins"] = cls._split_csv(data["cors_allowed_origins"])
        return data

    @model_validator(mode="after")
    def _reject_wildcard_with_credentials(self) -> ApiSettings:
        if self.cors_allow_credentials and "*" in self.cors_allowed_origins:
            raise ValueError(
                "CORS_ALLOW_CREDENTIALS=true cannot be combined with a wildcard "
                "('*') entry in CORS_ALLOWED_ORIGINS - this would let any origin "
                "make credentialed requests. List explicit allowed origins instead."
            )
        return self


def load_api_settings() -> ApiSettings:
    return ApiSettings()
