# ops

Infrastructure for Phase 1 Step 5 (PostgreSQL + Redis + database roles).

- `sql/01_bootstrap_roles.sh` + `sql/roles_and_schemas.sql.tmpl` - role,
  schema, and least-privilege grant bootstrap, run automatically by
  postgres's `docker-entrypoint-initdb.d` mechanism (see
  `../docker-compose.yml` and `../docker-compose.test.yml`).
- `docker/Dockerfile.test` - self-contained image for the `test-runner`
  service in `../docker-compose.test.yml`.
- `docker/test.env.example` - fixture-only credentials for the ephemeral
  test stack (never used by `../docker-compose.yml`).
- `scripts/` - created empty in Phase 1 Step 2; unused so far.

Actual Phase 1 application tables (users, trade_proposals, orders, ...)
belong to the migration step (Step 6+), not here - this step creates the
`core`/`audit`/`paper`/`live` schemas empty (see docs/schemas/README.md and
docs/adr/ADR-005-paper-live-isolation.md).
