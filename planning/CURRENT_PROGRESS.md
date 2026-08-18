# Current Progress

Last committed checkpoint: `9fb94cdaf2224db6951cb58a409429781437234f`
("feat: complete phase 1 paper execution gateway" - Step 9). Step 10
(below) is implemented on top of this commit but is **not yet committed**
- this document describes the current working tree, not a new checkpoint
commit.

**Numbering note**: an older, external "approved Phase 1 plan" numbered
steps differently (risk engine at its Step 11, intent minting at its Step
12, auth at its Step 13, the paper gateway at its Step 14). Actual execution
compressed this - the risk engine and `ApprovedOrderIntent` landed in Step 4,
auth in Step 8. From Step 9 onward, "Step N" refers to the sequence actually
committed in this repository, not the old external plan's numbering. Several
`docs/schemas/*.md`/ADR citations of the old numbering were reconciled to
the real step during Step 9; any remaining `docs/`-level citation of a
double-digit "Step N" for work not yet built should be read as "a future
step," not a literal promise about which step number will do it.

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
- Step 9 PAPER execution gateway (ADR-011)
- Step 10 PAPER trade-proposal intake and ledger API (ADR-012)

## Current repository state

- Paper-only
- Live trading structurally impossible
- No Kite adapter
- No broker credentials
- No LLM
- No market-data implementation
- No backtesting
- No automated strategies (proposals are still human-created; no strategy
  registry/signal engine exists to generate one)
- Step 9's `atp_api` diff was zero (ADR-011 §2 D2); Step 10 adds `atp_api`'s
  first business mutation route, `POST /api/v1/paper/proposals`, and it
  performs no risk evaluation of its own (ADR-012)
- PAPER trade-proposal intake and ledger API implemented (Step 10, ADR-012):
  `POST /api/v1/paper/proposals` (`atp_api.routers.paper`,
  `atp_api.services.paper_proposals`) closes the gap Step 9 left open -
  `paper.trade_proposals` had no production writer, so `atp_exec_paper`'s
  ADR-011 claim loop had zero real candidates to find. Intake performs
  **structural validation only**: request DTO parsing, `TradeProposal`'s
  own `__post_init__` invariants, and an `instrument_id` existence check
  against `core.instruments` - it never imports `atp_domain.intents` and
  never calls `atp_domain.risk.engine.evaluate`/`mint_intent_for_decision`
  (mechanically asserted, `tests/safety/test_proposal_intake_is_not_a_risk_gate.py`).
  `mode`, `proposal_id`, and `created_at` are always server-set;
  `created_by` is the authenticated principal's `user_id` - none of the
  four is ever caller-supplied. Idempotency is enforced by
  `paper.trade_proposals`' `UNIQUE (client_request_id)` constraint (insert,
  catch `IntegrityError`, compare - never check-then-insert, the Step 9
  lesson): an identical resubmission replays `200` with the original
  `proposal_id`; a conflicting one gets `409`. CSRF-protected exactly like
  `POST /api/v1/auth/logout` (`atp_api.deps.enforce_csrf`, a new reusable
  dependency). Five new GET routes read the result back without ever
  touching `atp_exec_paper`: `GET /api/v1/instruments` (existence-check
  data source, also user-facing), `GET /api/v1/paper/proposals[/{id}]`
  (nests the resulting `RiskDecision`/`Order`/`Fill` once
  `atp_exec_paper` has evaluated/executed them - `None` until then, never
  an error), `GET /api/v1/paper/positions`, `GET /api/v1/paper/cash`. No
  route path contains `order`, `execute`, or `/live`
  (`tests/safety/test_no_execution_path_in_api.py`'s pre-existing
  substring ban, preserved by nesting order/fill state inside the
  proposal-detail response instead of a `/paper/orders` route). Three new
  `Permission` members (`READ_INSTRUMENTS`, `SUBMIT_PAPER_PROPOSAL`,
  `READ_PAPER_LEDGER`), granted as one reusable
  `_PAPER_TRADING_PERMISSIONS` frozenset to `paper_trader`, `live_trader`,
  and `administrator` alike (`atp_api.security.rbac`) - `live_trader`
  remains permission-identical to `paper_trader` (still zero live-execution
  capability; submitting a PAPER proposal is not one). `execution/paper/`
  (`atp_exec_paper`) carries a **zero diff** for this milestone - the
  claim loop itself is unchanged; it simply now has real rows to find.
- PAPER execution gateway implemented (Step 9, ADR-011):
  `execution/paper/src/atp_exec_paper/` is no longer an empty stub -
  `simulator.py` (deliberately fake fill simulator), `risk_runner.py`
  (authoritative `RuleContext` assembly), `gateway.py` (the
  `TradeProposal -> RiskDecision -> ApprovedOrderIntent -> Order -> Fill ->
  Position -> Cash ledger -> Audit` pipeline, one `PaperExecutionUnitOfWork`
  transaction per proposal), `kill_switch_adapter.py` (DB -> domain
  fail-closed `SwitchState` mapping), `uow.py` (a dedicated Unit of Work
  exposing only the repositories `atp_paper_exec` is actually granted), and
  `__main__.py` (`python -m atp_exec_paper` - poll loop or one-shot). The
  gateway's only external input is a bare `proposal_id`; every other order
  field is reloaded from the database itself
  (`tests/safety/test_no_execution_path_in_atp_exec_paper.py`). Invoked only
  by a DB-polled claim loop (ADR-011) - never imported by `atp_api`, never
  reachable over HTTP, never routed through the worker. A PAPER MARKET
  proposal is rejected deterministically by a new seventh real PAPER rule
  (`RISK.DATA.001`/`PricedReferenceRule`,
  `atp_domain.risk.catalog.data_rules`) - no reference price is ever
  invented. Six new repositories
  (`order_intents`/`fills`/`positions`/`cash_ledger`/`instruments`/`risk_config`)
  back it; migration `0004_paper_cash_ledger_seed` seeds the opening PAPER
  cash `DEPOSIT` (`PAPER_INITIAL_CAPITAL = 10,000,000` -
  `docs/schemas/cash_ledger.md`'s explicit classification: a Phase-1
  deterministic paper-trading fixture only, not a real brokerage balance,
  not a production risk limit, not an assumption about any user's actual
  capital; not configurable, no environment variable).
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
- ADR-012 (Step 10): proposal intake is deliberately not a risk gate - a
  2xx from `POST /api/v1/paper/proposals` means recorded, not approved.
  `atp_api` may call neither `atp_domain.risk.engine.evaluate` nor
  `mint_intent_for_decision`, and may not import `atp_domain.intents` at
  all - but `atp_domain.risk.engine.RiskDecision` (a plain, frozen
  dataclass, not an operation) is a legitimate import for
  `atp_api.services.paper_ledger`'s read-only ledger view; reading a
  decision someone else computed is not evaluating one. Intake is also not
  gated on the kill switch - an engaged `PAPER` switch instead produces a
  persisted, auditable `RiskDecision` from `atp_exec_paper`, keeping
  exactly one authoritative risk boundary rather than two.
- The instrument-existence check in `POST /api/v1/paper/proposals` runs on
  a separate read-only session from the `UnitOfWork` the insert itself
  uses (`atp_api.deps.get_instrument_repository` vs `get_unit_of_work`) -
  `persistence/src/atp_persistence/db.py` is unmodified by Step 10; a
  plain read has no need of a write transaction, and `core.instruments`
  has no Phase 1 delete route for the two reads to race against.
- `atp_persistence.repositories.trade_proposals.list_for_mode`/
  `fills.list_by_order`/`positions.list_all`/`instruments.list_active` are
  Step 10's only persistence-layer additions - all additive read methods
  on existing repository classes; no new table, no new migration.

## Completed verification

- Test count grew again at the Step 10 checkpoint: new no-DB tests under
  `tests/unit/api/` (`test_instruments.py`, `test_paper_proposals.py`,
  `test_paper_ledger.py`, plus `fakes.py`/`conftest.py` extensions for the
  six new read-repository fakes and a `FakeTradeProposalRepository` that
  raises a real `sqlalchemy.exc.IntegrityError` on a duplicate
  `client_request_id`), `tests/safety/test_proposal_intake_is_not_a_risk_gate.py`
  (AST import/call scan plus schema-shape assertions), and reconciliation
  edits to `tests/safety/test_no_execution_path_in_api.py`,
  `tests/safety/test_rbac_server_side.py`, `tests/unit/api/test_routing.py`,
  and `tests/unit/security/test_rbac.py` for the new route/permission
  surface; +3 Docker-gated integration tests in
  `tests/integration/db/test_paper_proposal_intake.py` (real-`atp_api`-role
  round trip, genuine `UNIQUE(client_request_id)` concurrency via
  `asyncio.gather`, and the full intake -> `atp_exec_paper.run_once` ->
  ledger-read loop).
- ruff format / ruff check / mypy --strict / import-linter (4/4 kept,
  unchanged and unrelaxed) / pytest (455 passed, 77 skipped) / pre-commit
  (14 hooks incl. gitleaks) all clean at the Step 10 checkpoint - see the
  Step 10 implementation report for this session's actual run results.
  `git diff --stat -- execution/paper` is empty.
- Docker-dependent tests skipped when Docker is unavailable - not run, not
  faked as passing. The full intake path, RBAC matrix (including the new
  `SUBMIT_PAPER_PROPOSAL`/`READ_PAPER_LEDGER`/`READ_INSTRUMENTS`
  permissions), idempotency/replay/conflict behavior, and CSRF enforcement
  are exercised without Docker through `tests/unit/api/fakes.py`'s
  extended fake set; the genuine real-PostgreSQL round trip (the real
  `atp_api` role's actual grants, and true `asyncio.gather` concurrency
  across two connections) still needs
  `tests/integration/db/test_paper_proposal_intake.py`, skip-gated exactly
  like every other Step 5+ Docker-dependent test.

### Step 9 checkpoint verification (retained for history)

- Test count grew again at the Step 9 checkpoint: new no-DB tests under
  `tests/unit/exec_paper/` (`fakes.py` in-memory `PaperExecutionUnitOfWork`
  double + `test_gateway.py`/`test_risk_runner.py`/`test_simulator.py`/
  `test_kill_switch_adapter.py`), `tests/unit/domain/` (the new
  `RISK.DATA.001`/MARKET-rejection test, registry/engine count updates),
  `tests/unit/persistence/` (mapper + migration-chain updates), and
  `tests/safety/test_no_execution_path_in_atp_exec_paper.py`; +3 Docker-gated
  integration tests in `tests/integration/db/test_paper_execution_gateway.py`
- ruff format / ruff check / mypy --strict / import-linter / pytest /
  pre-commit / gitleaks: see the Step 9 implementation report for this
  session's actual run results
- Docker-dependent tests skipped when Docker is unavailable - not run, not
  faked as passing. The gateway's full logic (approval, MARKET rejection,
  kill-switch fail-closed behavior, insufficient-cash rejection, and a
  simulated claim race via a fake that raises a real
  `sqlalchemy.exc.IntegrityError` on a duplicate `risk_decisions` save) is
  exercised without Docker through `tests/unit/exec_paper/fakes.py`. The
  genuine real-PostgreSQL concurrency proof (two actually-concurrent
  `run_once` calls over two separate connections, real `atp_paper_exec`
  grants) still needs `tests/integration/db/test_paper_execution_gateway.py`,
  skip-gated exactly like every other Step 5+ Docker-dependent test.

## Known follow-ups

- No password-change route exists yet - a bootstrap admin's
  `must_change_password=True` is surfaced in `GET /api/v1/auth/me` but
  nothing yet lets them act on it. `POST /api/v1/paper/proposals` (Step 10)
  is the first mutation route beyond login/logout, so this is no longer
  blocked on "no mutation route exists" - just not yet built.
- `tests/safety/README.md`'s Step 10 reconciliation found two invariants
  genuinely still missing (not merely mislabeled): #3
  (`test_no_foreign_key_crosses_mode_schemas` - no test exists; trivially
  true today only because `live` holds zero tables) and #8
  (`test_secret_never_appears_in_logs` - only the redaction *function* is
  unit-tested, not the actual structlog pipeline end to end).
- `GET /api/v1/paper/proposals`'s list view calls
  `risk_decisions.get_by_proposal`/`orders.get_by_proposal`/
  `fills.list_by_order` once per returned proposal (`paper_ledger.list_proposals`)
  - an N+1 query pattern, acceptable at Phase 1's scale (`DEFAULT_PAGE_SIZE
  = 50`) but a candidate for a joined/batched read if the ledger ever needs
  to page through significantly more rows.
- `paper.trade_proposals.strategy_id`/`source_signal_id` remain unset by
  intake (always `None`) - Step 10 intentionally does not pre-empt the
  still-nonexistent strategy/signal engine; those columns attach later with
  no migration once one exists.
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
- `atp_domain.ports.storage` now declares seven repository protocols:
  the original four, plus `OrderIntentRepository`/`FillRepository`/
  `PositionRepository` (Step 9) - `Fill`/`Position`/`ApprovedOrderIntent`
  are genuine domain types their repository is the unit of work for, so
  they got Protocols; `cash_ledger`/`instruments`/`risk_config` got
  persistence-layer-only classes instead (no domain type exists for a cash
  ledger entry, and reading `RiskConfig`/instrument lot-tick data is a
  lookup, not a domain operation), mirroring the
  `SqlAlchemyKillSwitchStateRepository` precedent. `core.job_queue` still
  has ORM models and migration DDL but no repository at all - `atp_worker`
  remains unbuilt.
- `atp_exec_paper` does not reuse `atp_persistence.db.UnitOfWork` - a
  dedicated `PaperExecutionUnitOfWork` (`atp_exec_paper.uow`) exposes only
  the repositories `atp_paper_exec`'s DB role actually holds grants for,
  deliberately excluding `users`/`sessions` (ADR-011, migration 0003).
- ADR-011: the paper execution gateway is invoked by a DB-polled claim
  loop, not an in-process call, HTTP request, or worker job (no invocation
  mechanism existed for any of those without violating an existing
  contract or grant boundary). Exclusivity between concurrent claimants is
  the `UNIQUE (proposal_id)` constraint on `paper.risk_decisions`, not a
  row lock - `atp_paper_exec` holds only `SELECT` on
  `paper.trade_proposals` (migration 0003), and PostgreSQL requires
  `UPDATE`/`DELETE` privilege for `SELECT ... FOR UPDATE`, so a literal
  row-locking claim was not implementable without widening a grant
  deliberately narrowed in Step 6/7 - see ADR-011 for the full reasoning
  and the explicit user decision behind it.
- `backend/src/atp_api/main.py`'s `uvicorn.run` entrypoint has not been
  exercised end-to-end (no port actually bound/served in this
  environment) - only `create_app()` + `TestClient` were exercised.
- Rate limiting is in-process, in-memory only (Step 7 scope) - not shared
  across multiple `atp_api` worker processes; a Redis-backed
  `RateLimiter` implementation is deferred, per the Step 7 task's own
  scope note.

## Next implementation step

STEP 11 - not yet scoped in this document. Candidates surfaced during the
Step 10 reconciliation but deliberately not started: `atp_worker` (session
reap / audit integrity check / retention jobs - the strongest fully-
unblocked runner-up; `atp_worker` retains real `UPDATE` privilege on
`core.job_queue`, unlike ADR-011's situation, so a genuine
`SELECT ... FOR UPDATE SKIP LOCKED` claim loop is possible there); a
frontend scaffold (Node/npm not installed in this environment, and there
is now a real API surface worth rendering); or a market-data/egress policy
decision (blocked simultaneously by import-linter contract #4's no-egress
rule, `Settings` refusing to start with any `KITE_*` var, and ADR-006's
unreviewed-MCP gate - starting it is a phase boundary needing its own ADR
and explicit authorization, not something to slip into a step). Do not
begin without an explicit instruction and a fresh read of CLAUDE.md and the
relevant ADRs/rules.

## Critical instruction

Do not start live trading or broker integration.
Do not reinterpret skipped Docker tests as passing.
