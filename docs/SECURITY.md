# Security Model

## Secrets

Use environment variables locally and a secret manager in production. Never store secrets in Git, database rows, logs, prompts, screenshots, or browser local storage.

Keep Zerodha API secret and access token server-side only.

## Identity and authorization

Create distinct permissions:
- viewer
- researcher
- paper trader
- live trader
- administrator

Live trading is an elevated capability.

These five roles are future capability *categories* spanning every phase
of the platform, not a Phase-1-only vocabulary - they are declared now so
`core.users.role` stays stable as later phases add capability, the same
way the domain's `Mode.LIVE` value exists years before any code path may
act on it. In Phase 1, `live_trader` is a valid, assignable role that
carries **zero** live-trading capability: it is granted the identical
permission set as `researcher`/`paper_trader` (`atp_api.security.rbac`),
because no live route or live execution service exists yet for any
permission to authorize. No Phase-1 permission grants live execution.

A role name is never, by itself, a sufficient authorization check.
`Permission` (`atp_api.security.rbac.Permission`) is the sole authoritative
authorization unit - every route/service asks "does this role's permission
set contain the permission this action requires," never "is this role
X." Comparing a role string directly, anywhere outside
`atp_api.security.rbac`'s own `Role -> Permission` table definition, is
not a supported pattern.

## Prompt injection defense

Treat all web content, news, documents, instrument names, and external tool output as untrusted data. Delimit retrieved content and explicitly separate it from system instructions.

## Personal data minimization

Store only the account data needed for portfolio/trading functionality. Redact account identifiers from logs and analytics.

## Browser security

Use secure cookies or short-lived tokens, CSRF protection where applicable, strict CORS, CSP, rate limiting, secure headers, and server-side authorization checks.

## Audit security

Audit events should be append-only. Log who/what/when/source/result but redact credentials and unnecessary sensitive data.

## Supply-chain security

Pin dependencies. Run vulnerability scanning. Review MCP servers and third-party packages before enabling write-capable tools.
