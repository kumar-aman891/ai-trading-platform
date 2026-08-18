# Threat Model — Phase 1

Scope: the foundation/safety build only (no market data, no LLM, no broker
integration, no live execution — see `docs/ROADMAP.md` Phase 0 / backlog
P0). This document is reviewed and extended as each later phase adds real
attack surface (network egress, an LLM in the loop, real broker
credentials).

## Assets

| Asset | Where | Sensitivity |
|---|---|---|
| Session credentials (password hashes, session IDs) | `core.users`, `core.sessions` | High — account takeover |
| Audit/decision history | `audit.audit_events` | High — integrity, not confidentiality |
| Simulated capital/positions | `paper.*` | Low — no real money, but a false sense of P&L is itself a risk (docs/BACKTESTING.md) |
| Application secrets (`SESSION_SECRET_KEY`, DB/Redis credentials) | environment / secret provider | High |

**Not yet an asset in Phase 1** (because it doesn't exist yet): broker
credentials, LLM API keys, real market data, real capital. Their absence is
itself a Phase 1 control — see `docs/adr/ADR-006-mcp-read-only.md`.

## Trust boundaries

```
Internet
   │  (HTTPS, cookie auth, CSRF, CORS, rate limiting)
   ▼
atp_api  ──── DB role: atp_api (zero privileges on `live` schema)
   │  (internal Docker network only, {proposal_id} payload only)
   ▼
atp_exec_paper  ──── DB role: atp_paper_exec, no host port, no egress
   │
   ▼
PostgreSQL / Redis (internal network only, no host port)
```

`atp_worker` sits beside `atp_exec_paper`, with its own DB role and no
order-path privileges. No component in Phase 1 has outbound internet
access — verified by the `no HTTP client library` import-linter contract
(root `pyproject.toml`).

## Threats considered (STRIDE-lite) and Phase 1 mitigation

| Threat | Scenario | Mitigation |
|---|---|---|
| Spoofing | Attacker forges a session | HttpOnly/Secure/SameSite cookie, hashed session storage, CSRF double-submit |
| Tampering | Attacker or bug mutates an audit row | Revoked grants + rejecting trigger (ADR-010) |
| Tampering | Attacker submits raw order params to the executor | Executor accepts `{proposal_id}` only; `ApprovedOrderIntent` is the only thing a broker-facing method could ever accept (ADR-008) |
| Repudiation | "The AI placed that order, not me" / order without a trail | Audit write shares a transaction with the state change (ADR-010); every AI action carries a decision ID (CLAUDE.md #9) |
| Information disclosure | Secret leaks into logs/audit payload | Single redaction processor used by both logger and audit writer; dedicated test (`tests/safety/test_secret_never_appears_in_logs.py`, Phase 1 Step 11 — proves this end to end through the real logging pipeline, not just the redaction function in isolation) |
| Information disclosure | LIVE-scoped data reachable through the PAPER-scoped API role | Zero grants on `live` schema for `atp_api`/`atp_exec_paper` (ADR-005), mechanically tested |
| Denial of service | Order-rate flooding | `API_EXECUTION` kill switch, order-rate limit (rule catalog; not the primary Phase 1 focus — no internet-facing order-heavy surface exists yet) |
| Elevation of privilege | `paper_trader` reaching `administrator`-only actions (e.g. disengaging `GLOBAL_LIVE`) | Server-side RBAC on every route, tested by the full route×role matrix |
| Elevation of privilege | An AI-originated proposal reaching the broker without deterministic approval | No code path exists to do this — no `execute` capability is exposed to the AI layer at all in Phase 1 (there is no AI layer yet) |

## Explicitly out of scope for Phase 1

- Prompt injection via untrusted news/instrument text — no AI/LLM component
  exists yet (`.claude/rules/04-ai.md` applies starting the AI plane, not
  Phase 1).
- Broker-session hijacking, static-IP compliance posture — no broker
  integration exists (`docs/COMPLIANCE.md`'s NSE/SEBI requirements apply
  once live execution is built, not before).
- Supply-chain review of the Kite MCP server — deferred until it is
  actually registered (ADR-006); `docs/SECURITY.md`'s "review MCP servers
  before enabling write-capable tools" rule gates that future decision.

## Residual risk accepted for Phase 1

- Hash-chaining of audit events (tamper *evidence* beyond the grant+trigger
  tamper *prevention* already in place) is deferred to Phase 4 (ADR-010).
  Rationale: Phase 1's threat model is a single-operator local deployment
  where the database-level controls already prevent mutation through the
  application; a determined attacker with raw DB superuser access defeats
  either control equally, and that access itself is out of scope here.
