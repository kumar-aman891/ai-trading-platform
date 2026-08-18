# Current Progress

Last verified commit: `a07f27648895daff56596139eb6551c9ed77932`
("docs: add current progress handoff"). Step 6 (below) is implemented on
top of this commit but is **not yet committed** - this document describes
the current working tree, not a new checkpoint commit.

## Completed

- Architecture and readiness review
- Phase 1 planning
- Steps 0-2
- Step 3 platform foundation
- Step 4 domain kernel
- Step 4 corrections
- Step 5 PostgreSQL/Redis infrastructure
- Step 6 persistence / Alembic migrations / concrete database tables
- Step 6 architecture reconciliation (two corrections: `core.risk_config.
  created_by` made nullable for the migration-seeded bootstrap row;
  `atp_domain.orders.Order.intent_id` added to the domain contract)

## Current repository state

- Paper-only
- Live trading structurally impossible
- No Kite adapter
- No broker credentials
- No LLM
- No market-data implementation
- No backtesting
- No automated strategies
- `core`/`audit`/`paper` tables exist (15 tables, SQLAlchemy 2.x typed
  declarative models under `persistence/src/atp_persistence/models/`);
  `live` schema exists, empty, ungranted (ADR-005 §5.4)
- Alembic migration chain: `0001_core_audit_paper_schema`,
  `0002_seed_fixture_instruments`, `0003_table_grants` - upgrade/downgrade
  both verified via `alembic ... --sql` offline rendering (Docker
  unavailable in this environment; never run against a live database here)
- Three repository implementations (`TradeProposalRepository`,
  `RiskDecisionRepository`, `OrderRepository` - the only three storage
  ports `atp_domain.ports.storage` declares) plus a `UnitOfWork` boundary
  in `atp_persistence.db`. `SqlAlchemyOrderRepository.save()` now matches
  `OrderRepository`'s Protocol signature exactly (no extra keyword
  argument) - `atp_domain.orders.Order` carries `intent_id` directly.
- `core.risk_config.created_by` is nullable; migration `0001` seeds
  exactly one bootstrap PAPER config row with `created_by = NULL`. No
  `core.users` row exists after a clean migration.

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

## Completed verification

- Current test count: 210 passed, 61 skipped (Docker-dependent only - up
  from 197/24 at the Step 5 checkpoint, and 209/54 at the initial Step 6
  checkpoint: the reconciliation added 1 no-DB mapper test
  (`risk_config_to_row` with `created_by=None`) and 7 Docker-gated
  integration tests (1 order-repository intent_id round trip + 6 in the
  new `test_risk_config_bootstrap.py`))
- ruff format / ruff check: clean
- mypy --strict: clean (67 source files)
- import-linter: 4/4 contracts kept
- pre-commit (14 hooks, incl. gitleaks): all passed (requires `uv` on
  PATH - the `mypy`/`import-linter` hooks shell out to it; this session's
  environment only has `uv` inside `.venv/Scripts`, not the system PATH)
- Docker-dependent tests (`tests/integration/db/`, some of
  `tests/unit/infra/`) skipped because Docker is unavailable in this
  environment - not run, not faked as passing. All new Step 6 DB/migration/
  grant/repository tests follow the same skip-gated pattern as the Step 5
  suite and have not been executed against a real PostgreSQL instance.

## Known follow-ups

- Actual Docker integration validation (`docker compose config`,
  startup/health, the full `tests/integration/db/` suite, including every
  new Step 6 migration/constraint/grant/repository test) - none of this has
  run against a real database in this environment
- CI database/Redis integration (services are not wired into
  `.github/workflows/ci.yml` yet)
- Stale ADR-008 documentation update (references a minting mechanism that
  was superseded by the capability/issuance design in
  `atp_domain.intents`)
- Revisit Docker hardening (`cap_drop`, `read_only`) once runnable
  application containers exist
- Three columns required by docs/schemas/ still have no field on their
  Step 4 domain dataclass, confirmed in the reconciliation review as
  deliberate (application/provenance metadata, not domain business state):
  `paper.trade_proposals.created_by`, `paper.fills.source`, and
  `core.risk_config.active`/`.created_by`. The concrete repositories/
  mappers take these as extra parameters rather than fabricating values.
  See `atp_persistence.mappers`'s module docstring.
  (`paper.orders.intent_id` was resolved - it is now a required field on
  `atp_domain.orders.Order`, not in this list.)
- `core.risk_config.config` is a generic JSON limits blob in the schema,
  but `atp_domain.risk.config.RiskConfig` only models `max_order_notional`
  - an acknowledged Step 4 completeness gap (Phase 1 only implements two
    capital/notional rules), not a placement error. Revisit if/when more
  of the risk rule catalog (daily loss, concentration, etc.) is actually
  implemented.
- `atp_domain.ports.storage` declares only three repository protocols
  (`TradeProposalRepository`, `RiskDecisionRepository`,
  `OrderRepository`); `paper.fills`/`positions`/`cash_ledger`,
  `core.instruments`/`users`/`sessions`/`risk_config`/`kill_switch_*`/
  `job_queue` have ORM models and migration DDL but no repository class -
  none was fabricated beyond what the domain layer declares needing.

## Next implementation step

STEP 7 - not yet scoped in this document; do not begin without an explicit
instruction and a fresh read of CLAUDE.md and the relevant ADRs/rules.

## Critical instruction

Do not start live trading or broker integration.
Do not reinterpret skipped Docker tests as passing.
