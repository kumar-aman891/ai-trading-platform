"""Argon2id password hashing (Phase 1 Step 8).

Parameters are explicit, not library defaults, and documented here rather
than left implicit - `docs/SECURITY.md`/`docs/schemas/user.md` require
Argon2id specifically. Verification (`verify_password`) is constant-time
by construction: `argon2.PasswordHasher.verify` always recomputes the full
hash and compares digests, so it takes the same time whether the first
byte or the last byte differs, and a caller here never short-circuits that
by comparing hash strings itself.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError

# OWASP-recommended Argon2id baseline for a server-side login path (not a
# high-throughput API): 19 MiB memory is the OWASP *minimum*; Phase 1 is a
# single-operator deployment with no realistic concurrent-login load, so a
# higher, still-conservative cost is used instead.
_TIME_COST = 3
_MEMORY_COST_KIB = 65536  # 64 MiB
_PARALLELISM = 4
_HASH_LEN = 32
_SALT_LEN = 16

_hasher = PasswordHasher(
    time_cost=_TIME_COST,
    memory_cost=_MEMORY_COST_KIB,
    parallelism=_PARALLELISM,
    hash_len=_HASH_LEN,
    salt_len=_SALT_LEN,
)

# A syntactically valid Argon2id hash of an unguessable, never-issued
# password - verified against on every "unknown username" login attempt so
# that path costs the same wall-clock time as a real "wrong password" path
# (see atp_api.services.auth), rather than returning early and leaking a
# timing signal that the username doesn't exist.
DUMMY_HASH = _hasher.hash("this-is-not-a-real-password-and-is-never-issued")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHash):
        return False
    return True
