"""Tests for atp_platform.redaction — key denylist + value-pattern matching."""

from __future__ import annotations

from atp_platform.redaction import REDACTED, redact_mapping, redact_text


def test_denylisted_key_is_wholly_redacted() -> None:
    result = redact_mapping({"password": "hunter2", "note": "fine"})

    assert result["password"] == REDACTED
    assert result["note"] == "fine"


def test_denylist_matches_by_substring_not_exact_name() -> None:
    result = redact_mapping({"session_secret_key": "abc", "kite_api_key": "xyz"})

    assert result["session_secret_key"] == REDACTED
    assert result["kite_api_key"] == REDACTED


def test_high_entropy_hex_value_is_redacted_even_under_a_safe_key() -> None:
    hex_token = "a" * 40  # hex-shaped, no hyphens

    result = redact_mapping({"message": hex_token})

    assert hex_token not in result["message"]
    assert REDACTED in result["message"]


def test_uuid_shaped_value_is_not_redacted() -> None:
    """Deliberate exclusion (see redaction.py's module docstring): IDs must
    stay visible in logs."""
    correlation_id = "3fa85f64-5717-4562-b3fc-2c963f66afa6"

    result = redact_mapping({"correlation_id": correlation_id})

    assert result["correlation_id"] == correlation_id


def test_jwt_shaped_value_is_redacted() -> None:
    # Synthetic JWT fixture (the standard jwt.io example token), not a real credential.
    fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"  # gitleaks:allow

    result = redact_mapping({"message": f"token was {fake_jwt}"})

    assert fake_jwt not in result["message"]


def test_nested_structures_are_redacted() -> None:
    nested = {"outer": {"password": "hunter2", "items": ["ok", {"api_key": "zzz"}]}}

    result = redact_mapping(nested)

    assert result["outer"]["password"] == REDACTED
    assert result["outer"]["items"][1]["api_key"] == REDACTED
    assert result["outer"]["items"][0] == "ok"


def test_tuple_values_are_redacted_element_wise() -> None:
    hex_token = "b" * 40

    result = redact_mapping({"pair": ("safe", hex_token)})

    assert result["pair"] == ("safe", REDACTED)
    assert isinstance(result["pair"], tuple)


def test_redact_text_helper_redacts_free_text() -> None:
    text = "boom: " + "f" * 40

    redacted = redact_text(text)

    assert "f" * 40 not in redacted
    assert REDACTED in redacted
