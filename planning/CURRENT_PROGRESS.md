# Current Progress

Checkpoint commit: `79ab13e1039a7a1dcfb9d644b1e6cb2934638dd8`
("feat: complete phase 1 foundation steps 0-5")

## Completed

- Architecture and readiness review
- Phase 1 planning
- Steps 0-2
- Step 3 platform foundation
- Step 4 domain kernel
- Step 4 corrections
- Step 5 PostgreSQL/Redis infrastructure

## Current repository state

- Paper-only
- Live trading structurally impossible
- No Kite adapter
- No broker credentials
- No LLM
- No market-data implementation
- No backtesting
- No automated strategies

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

## Completed verification

- Current test count: 197 passed, 24 skipped (Docker-dependent only)
- ruff format / ruff check: clean
- mypy --strict: clean (53 source files)
- import-linter: 4/4 contracts kept
- pre-commit (14 hooks, incl. gitleaks): all passed
- Docker-dependent tests (`tests/integration/db/`, some of
  `tests/unit/infra/`) skipped because Docker is unavailable in this
  environment - not run, not faked as passing

## Known follow-ups

- Step 6 persistence/migrations
- Table-level grants (current grants are schema-level defaults - see
  `ops/sql/roles_and_schemas.sql.tmpl`)
- Actual Docker integration validation (`docker compose config`,
  startup/health, the full `tests/integration/db/` suite)
- CI database/Redis integration (services are not wired into
  `.github/workflows/ci.yml` yet)
- Stale ADR-008 documentation update (references a minting mechanism that
  was superseded by the capability/issuance design in
  `atp_domain.intents`)
- Revisit Docker hardening (`cap_drop`, `read_only`) once runnable
  application containers exist

## Next implementation step

STEP 6 - persistence / Alembic migrations / concrete database tables.

## Critical instruction

Do not start live trading or broker integration.
Do not reinterpret skipped Docker tests as passing.
