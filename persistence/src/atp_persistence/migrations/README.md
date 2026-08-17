# Migrations

Deliberately empty in Phase 1 Step 2. `alembic init` and migration 0001
(`core` + `audit` + `paper` schemas) and 0002 (`live` — empty, ungranted) are
Phase 1 Steps 6–7, which require a running PostgreSQL instance and are out of
scope for this step.

See `docs/schemas/` for the entity specifications these migrations will
implement, and `ops/sql/bootstrap_roles.sql` (also deferred — Step 5) for the
per-service database roles referenced throughout those specs.
