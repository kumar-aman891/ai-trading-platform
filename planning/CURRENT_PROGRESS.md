# Current Progress

Last committed checkpoint: `9addfd7` ("fix: update the stale head-version
assertion for migration 0005" - Step 12 Phase B Step 2, migration
`0005_job_queue_claim_constraints`). Step 12 Phase B's remaining work -
`atp_worker`'s repositories, runtime core (`errors.py`/`registry.py`/
`uow.py`/`runner.py`), all three handlers, `scheduler.py`, `__main__.py`,
ADR-013 §6a's window-attestation cadence resolution, safety invariant
#17, import-linter contract 5, and this section's own documentation
cleanup - is implemented in the working tree as of this section but is
**not yet committed**.

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
- Step 11 verification harness integrity (no ADR - see below)
- Step 12 Phase A - data-plane verification: the 78-test Docker-gated
  integration suite genuinely passes against real PostgreSQL/Redis,
  `continue-on-error` removed from CI's `integration` job (see below)
- Step 12 Phase B - `atp_worker` (ADR-013 "Operational Worker Scope"):
  implemented in the working tree, not yet committed (see below)

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

- `paper.trade_proposals.strategy_id`/`source_signal_id` remain unset by
  intake (always `None`) - Step 10 intentionally does not pre-empt the
  still-nonexistent strategy/signal engine; those columns attach later with
  no migration once one exists.
- ✅ Resolved (Phase 1 Step 18): `atp_api.bootstrap`'s CLI entrypoint
  (`python -m atp_api.bootstrap`) is now exercised as a real subprocess
  invocation against a real, migrated database -
  `tests/integration/db/test_bootstrap_subprocess.py` proves the first
  invocation succeeds and creates the administrator, a second invocation
  fails with no duplicate, and missing `BOOTSTRAP_ADMIN_*` env vars exit
  non-zero with the documented message and no traceback.
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

## Step 11 - verification harness integrity

A reconciliation pass ahead of scoping Step 11's original worker candidate
found that the integration-test harness itself reported a false pass:
`ops/scripts/run_integration_tests.sh` ran `pytest` on the **host**, but
`docker-compose.test.yml`'s `atp_test_internal` network is `internal: true`
with no published ports, so the host could never actually reach `postgres`/
`redis`. `tests/integration/db/conftest.py` converted every resulting
connection failure into `pytest.skip`, so `make test-integration` exited
`0` with all 77 tests skipped - a green run proving nothing, which is
exactly the failure mode this document's own "do not reinterpret skipped
Docker tests as passing" instruction (below) exists to prevent.

Step 11 fixed this without claiming to verify the data plane (Docker
remains unavailable in this environment):

- `ops/scripts/run_integration_tests.sh` now runs pytest **inside** the
  compose network via the `test-runner` service (`docker compose ... run
  --rm test-runner`), matching the invocation `docker-compose.test.yml`'s
  own header already documented, instead of on the host.
- `tests/integration/db/conftest.py` gained
  `ATP_REQUIRE_INTEGRATION_STACK=1`: when set, a missing `TEST_*` DSN or an
  unreachable instance calls `pytest.fail` instead of `pytest.skip`, so a
  broken/unreachable stack is reported as a failure. The default
  (unset/skip) behaviour for a plain local `pytest` run is unchanged.
  `tests/unit/infra/test_integration_stack_gate.py` proves this switch
  itself works, entirely without Docker.
- `.github/workflows/ci.yml` gained an `integration` job running `make
  test-integration` with `ATP_REQUIRE_INTEGRATION_STACK=1`, using
  `continue-on-error: true` as a **temporary** bridge (it has never passed
  in this environment) - removing that flag is Step 12's opening move.
- Two previously-missing safety-ledger invariants were folded in, both
  Docker-free: `test_no_cross_mode_foreign_keys.py` (#3, static
  `Base.metadata` walk) and `test_secret_never_appears_in_logs.py` (#8,
  proves redaction end-to-end through the real structlog pipeline,
  including a rendered exception traceback). See `tests/safety/README.md`.

**What remains true after Step 11, stated explicitly:** the 77
Docker-gated integration tests have still never executed against a real
PostgreSQL/Redis instance in this environment. Migrations, table grants,
append-only triggers, and RBAC-at-DB-level remain unverified. Step 11 made
the harness incapable of *lying* about that; it did not make the claim
true.

## Step 12 Phase A - data-plane verification

The gate Step 11 deferred: made the `integration` CI job genuinely pass and
removed `continue-on-error`. Took several iterations (see the `wip: step12
phase a iteration N` commits between `3f3bd26` and `671c370`/`00689db`),
absorbing migration DDL, compose networking, and grant/query interactions
that had never executed anywhere before this pass - exactly the outcome
this document's own "do not reinterpret skipped Docker tests as passing"
instruction existed to prevent from being papered over. Real, previously
-invisible bugs fixed along the way (`f383e3e` and follow-ups): a
`TestClient` needing a real IP for `core.sessions.ip_address INET`; audit
rows being append-only tripping cross-test connection reuse; user-teardown
ordering; a `Money` scale-cap overflow; several UUID/FK-ordering issues in
test fixtures themselves, not application code. `0003_table_grants.py`'s
`downgrade()` defect (a stray `GRANT UPDATE, DELETE, TRUNCATE ON
audit.audit_events` the Step 5 baseline never granted) was fixed with its
own regression test, per the reconciliation plan that scoped this phase.

**Result:** the integration job reports 78 passed, 0 skipped, against real
PostgreSQL/Redis, and `continue-on-error` is gone from
`.github/workflows/ci.yml`. Migrations 0001-0004, table grants, the
append-only triggers, and RBAC-at-DB-level are now genuinely verified, not
merely asserted in code.

## Step 12 Phase B - `atp_worker` (ADR-013 "Operational Worker Scope")

Built on Phase A's verified data plane, per the reconciliation plan's
"verify, then build" sequencing. ADR-013 written first, then implemented
in order: migration `0005_job_queue_claim_constraints` (three
constraints, zero new columns - `ux_job_queue_one_live_per_type`,
`terminal_state_has_completed_at`, `attempts_within_bounds`; committed and
verified against real PostgreSQL, `9addfd7`); `repositories/jobs.py` and
`repositories/session_observations.py`; the worker runtime core
(`errors.py`, `registry.py`, `uow.py`, `runner.py` - three-transaction
claim protocol, ADR-013 §3); all three handlers
(`AUDIT_INTEGRITY_CHECK`/`RETENTION`/`SESSION_REAP`); `scheduler.py` and
`__main__.py`.

One genuine ADR defect was found and corrected during implementation
review, not silently patched: ADR-013's original §6 ("every window
attested exactly once, no gap and no overlap") contradicted its own §2
("a later run over the same window... compares it against the attested
value") - under the original wording, no window was ever re-attested, so
`AUDIT_INTEGRITY_CHECK`'s tamper-detection path was unreachable in
production despite being fully implemented and tested. ADR-013 §6a now
specifies exact integer arithmetic (a 900s window, a 300s tick, each
window attested three times at +0/+20/+40 minutes after it closes,
verified by direct simulation before being trusted) rather than the
contradictory prose it replaced.

Safety invariant #17 (`tests/safety/test_no_execution_path_in_worker.py`,
`tests/safety/README.md`) and import-linter contract 5 (`atp_worker`
forbidden from importing `atp_api`/`atp_exec_paper`, redundant with the
layered contract by design, matching contract 3's precedent) close out
Phase B's boundary-proving work. `docs/schemas/session.md`,
`docs/schemas/job_queue.md`, `workers/pyproject.toml`'s description, and
`workers/src/atp_worker/__init__.py`'s stale "Step 16" reference are
corrected to match what ADR-013 actually authorizes - none of these were
behavior changes.

**Not yet done:** `tests/integration/db/test_worker_job_queue.py` is
written (concurrent claim exclusivity, lease reclaim, duplicate-live-job
rejection via the real repository, the narrow session-projection trap,
one full claim-to-terminal-state lifecycle - all through the real
`atp_worker` role) but, like every Docker-gated test in this repository,
has not executed here: Docker is not installed in this environment. It
will run for the first time in CI, the same way Phase A's 78 tests did.

## Next implementation step

Not yet decided. Step 12 Phase B is feature-complete in the working tree
but uncommitted - commit, push, and let CI run the Docker-gated worker
suite for the first time before deciding what comes next. Other candidates
remain deliberately not started: a frontend scaffold (Node/npm not
installed in this environment); a market-data/egress policy decision
(blocked simultaneously by import-linter contract #4's no-egress rule,
`Settings` refusing to start with any `KITE_*` var, and ADR-006's
unreviewed-MCP gate - starting it is a phase boundary needing its own ADR
and explicit authorization, not something to slip into a step). Do not
begin without an explicit instruction and a fresh read of CLAUDE.md and the
relevant ADRs/rules.

## Critical instruction

Do not start live trading or broker integration.
Do not reinterpret skipped Docker tests as passing.
Step 12 Phase A verified the pre-`atp_worker` data plane in CI (78 passed,
0 skipped, `continue-on-error` removed) - that verification is real and
does not need repeating for its own sake. It does **not** extend to
`atp_worker`: `tests/integration/db/test_worker_job_queue.py` exists but,
like every Docker-gated test authored in this environment, has not yet
executed anywhere. Do not reinterpret its existence as its result.
