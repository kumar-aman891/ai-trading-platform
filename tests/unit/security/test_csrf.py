"""CSRF: double-submit token generation and verification."""

from __future__ import annotations

from atp_api.security.csrf import csrf_tokens_match, generate_csrf_token


def test_generate_csrf_token_is_high_entropy() -> None:
    token = generate_csrf_token()
    assert len(token) >= 32


def test_generate_csrf_token_is_never_repeated() -> None:
    assert generate_csrf_token() != generate_csrf_token()


def test_csrf_tokens_match_when_header_and_cookie_both_equal_expected() -> None:
    token = generate_csrf_token()
    assert csrf_tokens_match(header_value=token, cookie_value=token, expected=token) is True


def test_csrf_tokens_match_false_when_header_missing() -> None:
    token = generate_csrf_token()
    assert csrf_tokens_match(header_value=None, cookie_value=token, expected=token) is False


def test_csrf_tokens_match_false_when_cookie_missing() -> None:
    token = generate_csrf_token()
    assert csrf_tokens_match(header_value=token, cookie_value=None, expected=token) is False


def test_csrf_tokens_match_false_when_header_and_cookie_disagree() -> None:
    expected = generate_csrf_token()
    assert (
        csrf_tokens_match(
            header_value=expected, cookie_value=generate_csrf_token(), expected=expected
        )
        is False
    )


def test_csrf_tokens_match_false_when_neither_matches_expected() -> None:
    expected = generate_csrf_token()
    forged = generate_csrf_token()
    assert csrf_tokens_match(header_value=forged, cookie_value=forged, expected=expected) is False


def test_csrf_tokens_match_false_for_empty_strings() -> None:
    token = generate_csrf_token()
    assert csrf_tokens_match(header_value="", cookie_value=token, expected=token) is False
