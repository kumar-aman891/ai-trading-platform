"""AUTHENTICATION: Argon2id password hashing/verification."""

from __future__ import annotations

from atp_api.security.passwords import DUMMY_HASH, hash_password, verify_password


def test_hash_password_never_returns_the_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in hashed


def test_hash_password_is_argon2id() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed.startswith("$argon2id$")


def test_verify_password_accepts_the_correct_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password(hashed, "correct horse battery staple") is True


def test_verify_password_rejects_the_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password(hashed, "wrong password") is False


def test_verify_password_rejects_a_malformed_hash() -> None:
    assert verify_password("not-a-real-argon2-hash", "anything") is False


def test_two_hashes_of_the_same_password_differ() -> None:
    """Argon2id salts every hash independently - two calls never produce
    the same stored value even for the same input password."""
    assert hash_password("same password") != hash_password("same password")


def test_dummy_hash_is_a_real_verifiable_argon2id_hash() -> None:
    """Used by atp_api.services.auth.login's constant-time floor for
    unknown usernames - must itself be verifiable (just never matching a
    real password), not a placeholder string."""
    assert DUMMY_HASH.startswith("$argon2id$")
    assert verify_password(DUMMY_HASH, "anything") is False
