"""Cookie names and attribute policy (Phase 1 Step 8).

Two cookies, two different policies:

- `SESSION_COOKIE_NAME`: HttpOnly (never readable by JavaScript, so an XSS
  payload cannot exfiltrate it), Secure, SameSite=Strict, Path=/. Carries
  only the opaque session token (`atp_api.security.tokens.generate_token`)
  - never a JWT, never anything self-describing.
- `CSRF_COOKIE_NAME`: Secure, SameSite=Strict, Path=/, but *not* HttpOnly -
  same-origin JavaScript must be able to read it to echo it back as a
  request header (`atp_api.security.csrf`'s module docstring explains why
  that's still safe against a cross-site attacker).

`Secure` is unconditional, not gated on `environment == "production"` like
`SecurityHeadersMiddleware`'s HSTS header - an auth cookie must never be
sent over plain HTTP in any environment (docs/SECURITY.md: "use secure
cookies"). Tests exercise this over an `https://` `TestClient` base URL
rather than relaxing the flag.
"""

from __future__ import annotations

from fastapi import Response

SESSION_COOKIE_NAME = "atp_session"
CSRF_COOKIE_NAME = "atp_csrf"


def set_auth_cookies(
    response: Response, *, session_token: str, csrf_token: str, max_age_seconds: int
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=max_age_seconds,
        path="/",
        httponly=True,
        secure=True,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=max_age_seconds,
        path="/",
        httponly=False,
        secure=True,
        samesite="strict",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/", secure=True, samesite="strict")
