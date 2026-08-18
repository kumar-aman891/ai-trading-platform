"""Authentication, session/CSRF handling, RBAC (Phase 1 Step 8).

- `passwords`: Argon2id hashing/verification.
- `tokens`: opaque session-token generation/hashing/constant-time compare.
- `csrf`: double-submit CSRF token generation/verification.
- `cookies`: cookie names and HttpOnly/Secure/SameSite attribute policy.
- `rbac`: the `Role -> Permission` model.

Nothing in this package touches a database or an `AsyncSession` - all I/O
(looking up a user, creating/revoking a session row, writing an audit
event) lives in `atp_api.services.auth`/`atp_api.services.sessions`, which
depend on these pure modules plus the Step 8 persistence repositories
(`atp_persistence.repositories.users`/`sessions`/`audit_writer`).

The HTTP security *header* baseline (CSP, X-Frame-Options, HSTS, etc.)
landed in Step 7 at `atp_api.middleware.security_headers` instead, since it
needs no identity system.
"""
