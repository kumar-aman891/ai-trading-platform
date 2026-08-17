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
