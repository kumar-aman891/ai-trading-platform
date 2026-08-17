# Secret Handling

Concrete rules implementing `docs/SECURITY.md`'s "Secrets" section for
Phase 1. Extended, not replaced, once a production secret manager and
broker/LLM credentials are actually introduced (Phase 4+/Phase 6).

## What is a secret in Phase 1

- `SESSION_SECRET_KEY`
- Database and Redis connection credentials (embedded in `DATABASE_URL` /
  `REDIS_URL`)
- User password hashes (sensitive, but not a "secret" the app holds on its
  own behalf — never logged, never returned by any API response, never
  reversible)

## What must never appear in Phase 1

- Any `KITE_*` variable, in any environment, including local `.env`.
  `atp_platform.config` refuses to start the process if one is present —
  Phase 1 has no legitimate use for a broker credential (ADR-006).
- Any `LLM_*`/model-provider API key — same refusal, same reason (no LLM
  integration exists).
- `TRADING_MODE=LIVE` — rejected at startup with an explicit "not
  implemented in this build" error, not a generic validation failure
  (ADR-005, ADR-008).

## Handling rules

1. **Local dev**: `.env`, gitignored, never committed. `.env.example`
   ships with placeholder values only and is the only env file tracked in
   git.
2. **Production** (not built in Phase 1): a secret manager behind the
   `atp_platform.secrets.SecretProvider` port — `EnvSecretProvider` is the
   only Phase 1 implementation; a manager-backed implementation slots in
   later without touching call sites.
3. **In memory**: secrets are held as `SecretStr` (or equivalent
   non-repr-leaking wrapper) everywhere in `atp_platform.config.Settings`.
   `Settings` overrides `__repr__`/`__str__` so accidental logging of the
   settings object cannot leak a value.
4. **In logs**: the redaction processor (`atp_platform.redaction`) runs
   last in the structlog pipeline and applies both a key denylist
   (`password`, `token`, `secret`, `api_key`, `access_token`,
   `authorization`, `cookie`, `csrf`, `session_id`) and value-pattern
   matching (JWT-shaped strings, high-entropy hex/base64 ≥32 chars). This
   is the same processor `audit.audit_events.payload` is written through —
   one implementation, two consumers.
5. **In git**: `gitleaks` runs both as a pre-commit hook
   (`.pre-commit-config.yaml`) and as a CI job
   (`.github/workflows/ci.yml`, `secret-scan`), independently of each
   other, so a bypassed local hook (`--no-verify`) is still caught before
   merge.
6. **In the database**: no secret is ever written to a database row in
   Phase 1. `core.users.password_hash` is a one-way Argon2id hash, not a
   secret the application can recover.
7. **Per-service least privilege**: each service's `Settings` class only
   declares the fields it needs. `atp_api`'s settings have no field for a
   broker credential — not "unused," genuinely absent from the type — so
   misconfiguration cannot hand the API service a capability it should
   never hold.

## Startup assertions (fail-fast; the process refuses to start otherwise)

- `TRADING_MODE` ∈ `{PAPER}`.
- No `KITE_*` or `LLM_*` environment variable is present.
- `SESSION_SECRET_KEY` is present, at least 32 bytes, and not equal to the
  placeholder value in `.env.example`.

These three assertions are exercised by
`test_settings_refuse_to_start_with_live_mode_or_broker_credentials`,
Phase 1 Step 3, one of the 16 safety-suite invariants.

## Rotation and incident response

Not built in Phase 1 (single-operator local deployment, no production
secret manager yet). `docs/OBSERVABILITY.md`'s "secret leakage detection"
alert and a rotation runbook are Phase 4+ prerequisites, tracked alongside
the live-execution deployment topology work in
`docs/runbooks/LIVE_ACTIVATION.md`.
