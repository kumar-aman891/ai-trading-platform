"""Opaque high-entropy tokens: session IDs and CSRF tokens share the same
generation/hashing/comparison primitives (Phase 1 Step 8).

Session IDs: `secrets.token_urlsafe(32)` (256 bits of entropy) is the raw
value returned to the browser only inside the HttpOnly cookie - never
logged, never stored. `hash_token` (SHA-256) is what actually persists in
`core.sessions.session_id_hash` (`docs/schemas/session.md`: "the raw
session ID is never stored").
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_TOKEN_ENTROPY_BYTES = 32


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
