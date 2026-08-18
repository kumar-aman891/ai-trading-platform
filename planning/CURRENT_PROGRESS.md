# Current Progress

Last committed checkpoint: `e8910c886e395a5b3df34ecd13a55d5f22fe4818`
("feat: complete phase 1 persistence and database layer" - Step 6 +
architecture reconciliation). Steps 7 and 8 (below) are implemented on top
of this commit but are **not yet committed** - this document describes the
current working tree, not a new checkpoint commit.

## Completed

- Architecture and readiness review
- Phase 1 planning
- Steps 0-2
- Step 3 platform foundation
- Step 4 domain kernel
- Step 4 corrections
- Step 5 PostgreSQL/Redis infrastructure
- Step 6 persistence / Alembic migrations / concrete database tables
- Step 6 architecture reconciliation (`core.risk_config.created_by` made
  nullable for the migration-seeded bootstrap row;
  `atp_domain.orders.Order.intent_id` added to the domain contract)
- Step 7 application/FastAPI foundation (read-only)
- Step 8 authentication, session management, RBAC, and CSRF

## Current repository state

- Paper-only
- Live trading structurally impossible
- No Kite adapter
- No broker credentials
- No LLM
- No market-data implementation
- No backtesting
- No automated strategies
- Authentication/authorization/session/CSRF/RBAC implemented (Step 8):
  Argon2id password hashing, opaque hashed-token server-side sessions
  (8h sliding TTL), double-submit CSRF, five-role RBAC with a central
  `Role -> Permission` model (`atp_api.security.rbac`) - `live_trader` is
  assignable but carries zero capability beyond the other observer roles,
  since no live route or live execution service exists to authorize.
  Bootstrap-admin is a one-time-secret-gated script
  (`python -m atp_api.bootstrap`), not an HTTP route.
- `core`/`audit`/`paper` tables exist (15 tables); `live` schema exists,
  empty, ungranted (ADR-005 §5.4)
- Alembic migration chain: `0001_core_audit_paper_schema`,
  `0002_seed_fixture_instruments`, `0003_table_grants`
- Four repository implementations: `TradeProposalRepository`,
  `RiskDecisionRepository`, `OrderRepository` (write paths, Step 6) and
  `AuditEventRepository` (read-only, Step 7) - the only four ports
  `atp_domain.ports.storage` declares - plus a `UnitOfWork` boundary and a
  `read_only_session` helper in `atp_persistence.db`.
  `SqlAlchemyKillSwitchStateRepository` is a Step 7 read-only addition
  with no matching domain Protocol (see its module docstring for why).
- `backend/src/atp_api/` is a working FastAPI application: `create_app()`
  factory, `/healthz`, `/readyz`, `GET /api/v1/system/status`,
  `GET /api/v1/kill-switches` (read-only), `GET /api/v1/audit/events`
  (read-only, paginated). Correlation-ID propagation, security headers,
  CORS (allow-list, wildcard+credentials rejected at config time),
  in-memory rate limiting, structured request logging, and a consistent
  error-response envelope are all wired in. `POST /api/v1/auth/login`,
  `POST /api/v1/auth/logout`, and `GET /api/v1/auth/me` (Step 8) are the
  only routes that accept a request body or use a method other than GET;
  every other route remains GET-only (verified by
  `tests/safety/test_no_execution_path_in_api.py`, updated for the Step 8
  allow-list). `system/status`, `kill-switches`, and `audit/events` now
  require authentication and an explicit `Permission`
  (`atp_api.deps.require_permission`).

## Important architectural decisions

- PAPER/LIVE separate schemas and service identities
- AI never executes orders
- Read-only Kite MCP in future AI/research layer
- Direct Kite execution gateway only in future live phase
- Deterministic risk engine
- ApprovedOrderIntent boundary
- PostgreSQL system of record
- Redis transient only
- Decimal only in domain
- SQLAlchemy 2.x
- Pure domain kernel
- Domain <-> ORM mapping is explicit (`atp_persistence.mappers`), never via
  ORM annotations on domain dataclasses (ADR-009)
- API dependency direction: router -> application service
  (`atp_api.services`) -> repository/Unit of Work -> domain. Routers never
  see a `Session`/ORM object; `atp_api.deps.get_db_session` is the only
  place a session is constructed, and it is never itself a route parameter.
- `atp_api.security/` now holds passwords/tokens/csrf/cookies/rbac (Step
  8); the Step 7 HTTP security *header* baseline stays in
  `atp_api.middleware.security_headers` (a distinct, still-separate
  concern needing no identity system).
- Session validation (`atp_api.deps.get_current_principal`) always uses
  the write-path `UnitOfWork` (`get_unit_of_work`), never the read-only
  `get_db_session` - a valid request slides the session's `expires_at`
  forward, which is a write, and that write shares a transaction with
  anything else the same request does (e.g. an `AUTHORIZATION_DENIED`
  audit write on an RBAC failure), per ADR-010.
- `core.users`/`core.sessions` get persistence-layer repositories
  (`atp_persistence.repositories.users`/`sessions`) with no matching
  `atp_domain.ports.storage` Protocol, deliberately mirroring the Step 7
  `SqlAlchemyKillSwitchStateRepository` precedent: authenticating a
  principal and mapping role to permission is an application/security
  concern, not domain trading logic.
- `SqlAlchemyAuditEventWriter` (`atp_persistence.repositories.audit_writer`)
  is a separate write-only class, not a `save` method added to the Step 7
  `AuditEventRepository` Protocol - that Protocol's own docstring states
  "no route anywhere writes through this port" as a deliberate Step 7
  decision, which Step 8 does not reopen.
- No `atp_domain` module was added for `User`/`Session` - RBAC
  (`Role`/`Permission`/`ROLE_PERMISSIONS`) lives entirely in
  `atp_api.security.rbac`, since Phase 1 has no other service that needs
  it (unlike `Money`/`RiskConfig`, which are genuine cross-cutting trading
  domain concepts).

## Completed verification

- Current test count: 366 passed, 71 skipped (Docker-dependent only) - up
  from 274/66 at the Step 7 checkpoint: +92 no-DB tests under
  `tests/unit/security/`, `tests/unit/api/` (new `fakes.py` in-memory
  `UnitOfWork` double + `test_auth_flows.py`/`test_bootstrap.py`/
  `test_auth_no_db.py`), and `tests/safety/`; +5 Docker-gated integration
  tests in `tests/integration/db/test_auth_flows.py`
- ruff format / ruff check: clean
- mypy --strict: clean (106 source files)
- import-linter: 4/4 contracts kept
- pre-commit (14 hooks, incl. gitleaks): all passed (requires `uv` on
  PATH - the `mypy`/`import-linter` hooks shell out to it; this session's
  environment only has `uv` inside `.venv/Scripts`, not the system PATH)
- Docker-dependent tests skipped because Docker is unavailable in this
  environment - not run, not faked as passing. Step 8's login/session/RBAC
  logic is instead exercised through real HTTP requests against the fully
  wired app with only the database dependency
  (`atp_api.deps.get_unit_of_work`) swapped for an in-memory fake
  (`tests/unit/api/fakes.py`) - genuine login/logout/session-expiry/
  sliding-renewal/CSRF/RBAC-matrix flows all run without Docker this way.
  A real-database round trip (real `SqlAlchemyUserRepository`/
  `SqlAlchemySessionRepository`, a genuinely-already-expired row, real
  table grants) still needs `tests/integration/db/test_auth_flows.py`,
  which remains skip-gated exactly like every other Step 5+ Docker-
  dependent test.

## Known follow-ups

- No password-change route exists yet - a bootstrap admin's
  `must_change_password=True` is surfaced in `GET /api/v1/auth/me` but
  nothing yet lets them act on it. Deferred rather than blocking login
  entirely (which would be a lockout, not a safety improvement) until a
  Step 9+ mutation route exists.
- Rate limiting for login remains in-process/in-memory
  (`ApiSettings.login_rate_limit_*`), same single-process caveat as the
  Step 7 general limiter.
- `atp_api.bootstrap`'s CLI entrypoint (`python -m atp_api.bootstrap`) has
  not been exercised as an actual subprocess invocation in this
  environment - only `bootstrap_admin()` itself (unit-tested against a
  fake `UnitOfWork`) and, when Docker is available,
  `tests/integration/db/test_auth_flows.py`'s direct async call.
- Actual Docker integration validation (`docker compose config`,
  startup/health, the full `tests/integration/db/` suite) - none of this
  has run against a real database in this environment
- CI database/Redis integration (services are not wired into
  `.github/workflows/ci.yml` yet)
- Stale ADR-008 documentation update (references a minting mechanism that
  was superseded by the capability/issuance design in
  `atp_domain.intents`)
- Revisit Docker hardening (`cap_drop`, `read_only`) once runnable
  application containers exist
- Three columns required by docs/schemas/ still have no field on their
  Step 4 domain dataclass, confirmed deliberate in the Step 6
  reconciliation review (application/provenance metadata, not domain
  business state): `paper.trade_proposals.created_by`,
  `paper.fills.source`, and `core.risk_config.active`/`.created_by`.
- `core.risk_config.config` is a generic JSON limits blob in the schema,
  but `atp_domain.risk.config.RiskConfig` only models `max_order_notional`
  - an acknowledged Step 4 completeness gap (Phase 1 only implements two
    capital/notional rules), not a placement error.
- `atp_domain.ports.storage` still declares only four repository
  protocols (unchanged by Step 8 - see "Important architectural
  decisions" above for why `users`/`sessions` got persistence-layer
  classes instead). `paper.fills`/`positions`/`cash_ledger`,
  `core.instruments`/`risk_config`/`kill_switch_*`/`job_queue` have ORM
  models and migration DDL but no repository class or Protocol at all -
  none was fabricated beyond what a real caller (Step 6/7/8) needed.
- `backend/src/atp_api/main.py`'s `uvicorn.run` entrypoint has not been
  exercised end-to-end (no port actually bound/served in this
  environment) - only `create_app()` + `TestClient` were exercised.
- Rate limiting is in-process, in-memory only (Step 7 scope) - not shared
  across multiple `atp_api` worker processes; a Redis-backed
  `RateLimiter` implementation is deferred, per the Step 7 task's own
  scope note.

## Next implementation step

STEP 9 - not yet scoped in this document. Do not begin without an explicit
instruction and a fresh read of CLAUDE.md and the relevant ADRs/rules.

## Critical instruction

Do not start live trading or broker integration.
Do not reinterpret skipped Docker tests as passing.
