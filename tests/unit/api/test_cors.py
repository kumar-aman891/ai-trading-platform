"""CORS: allowed origin succeeds, disallowed origin rejected, wildcard +
credentials configuration rejected at startup."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from atp_api.app import create_app
from atp_api.config import ApiSettings
from atp_platform.config import Settings


def test_allowed_origin_receives_cors_header(settings: Settings) -> None:
    api_settings = ApiSettings(cors_allowed_origins=("https://allowed.example",))
    client = TestClient(create_app(settings=settings, api_settings=api_settings))

    response = client.get("/healthz", headers={"Origin": "https://allowed.example"})

    assert response.headers.get("access-control-allow-origin") == "https://allowed.example"


def test_disallowed_origin_receives_no_cors_header(settings: Settings) -> None:
    api_settings = ApiSettings(cors_allowed_origins=("https://allowed.example",))
    client = TestClient(create_app(settings=settings, api_settings=api_settings))

    response = client.get("/healthz", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers


def test_no_origins_configured_means_no_cors_header_for_anyone(settings: Settings) -> None:
    client = TestClient(create_app(settings=settings, api_settings=ApiSettings()))

    response = client.get("/healthz", headers={"Origin": "https://anything.example"})

    assert "access-control-allow-origin" not in response.headers


def test_wildcard_origin_with_credentials_is_rejected_at_configuration_time() -> None:
    with pytest.raises(ValidationError, match="wildcard"):
        ApiSettings(cors_allowed_origins=("*",), cors_allow_credentials=True)


def test_wildcard_origin_without_credentials_is_permitted() -> None:
    api_settings = ApiSettings(cors_allowed_origins=("*",), cors_allow_credentials=False)
    assert api_settings.cors_allowed_origins == ("*",)


def test_comma_separated_env_style_origins_are_parsed() -> None:
    api_settings = ApiSettings(cors_allowed_origins="https://a.example,https://b.example")
    assert api_settings.cors_allowed_origins == ("https://a.example", "https://b.example")
