"""Double-submit CSRF (Phase 1 Step 8).

A CSRF token is minted once per session (at login) and stored on the
session row (`core.sessions.csrf_token`) - the *same* value is echoed to
the browser in a non-HttpOnly cookie so client-side JavaScript can read it
and send it back as a request header on every state-changing request. The
server then checks that the header value matches the session's stored
value. This is deliberately not the session ID itself: an attacker who
cannot read the HttpOnly session cookie also cannot read this one merely
by having the browser send it along (SameSite=Strict already blocks that
for cross-site requests) - the double-submit requires JavaScript running
on the *same* origin to have read the cookie and copied it into a header,
which a cross-site attacker's forged form/script cannot do.

`GET`/`HEAD`/`OPTIONS` requests never carry a CSRF check - CSRF only
matters for a request that changes state, and this codebase has exactly
two state-changing (POST) routes, handled differently on purpose:

- `POST /api/v1/auth/login` is CSRF-exempt. CSRF protection binds a token
  to an *existing* authenticated session; before login succeeds there is
  no session row, so no `csrf_token` has been minted for this caller to
  present yet - there is nothing to check. This is not a gap: login is
  instead defended by `SameSite=Strict` (a cross-site form/script cannot
  cause the browser to attach any of this origin's cookies to the forged
  request in the first place) and `atp_api.deps.enforce_login_rate_limit`
  (bounds how many attempts a login-CSRF-style attack could even try).
- `POST /api/v1/auth/logout` - the only state-changing route that *does*
  run with an authenticated session already in hand - is CSRF-protected
  unconditionally (`atp_api.services.auth.logout`, called from
  `atp_api.routers.auth.logout`, checks `csrf_tokens_match` before
  revoking anything). Every future authenticated state-changing route
  must follow the same rule this one already does: CSRF-exempt if and
  only if no session yet exists to bind a token to; CSRF-checked in every
  other case.
"""

from __future__ import annotations

import secrets

from atp_api.security.tokens import constant_time_equals

_TOKEN_ENTROPY_BYTES = 32


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)


def csrf_tokens_match(*, header_value: str | None, cookie_value: str | None, expected: str) -> bool:
    if not header_value or not cookie_value:
        return False
    return constant_time_equals(header_value, expected) and constant_time_equals(
        cookie_value, expected
    )
