"""SESSION: opaque token generation/hashing/constant-time comparison."""

from __future__ import annotations

from atp_api.security.tokens import constant_time_equals, generate_token, hash_token


def test_generate_token_is_high_entropy_and_url_safe() -> None:
    token = generate_token()
    assert len(token) >= 32
    assert all(ch.isalnum() or ch in "-_" for ch in token)


def test_generate_token_is_never_repeated() -> None:
    assert generate_token() != generate_token()


def test_hash_token_is_deterministic() -> None:
    token = generate_token()
    assert hash_token(token) == hash_token(token)


def test_hash_token_never_reveals_the_raw_token() -> None:
    token = generate_token()
    assert token not in hash_token(token)


def test_hash_token_differs_for_different_tokens() -> None:
    assert hash_token(generate_token()) != hash_token(generate_token())


def test_constant_time_equals_true_for_equal_strings() -> None:
    assert constant_time_equals("abc123", "abc123") is True


def test_constant_time_equals_false_for_different_strings() -> None:
    assert constant_time_equals("abc123", "abc124") is False
    assert constant_time_equals("short", "much-longer-string") is False
