# Migrations

Phase 1 Step 6. Three revisions, applied in order:

- `0001_core_audit_paper_schema` - every table in `core`/`audit`/`paper`
  (docs/schemas/), the append-only triggers for `audit.audit_events` and
  `core.kill_switch_history`, the immutability trigger for
  `core.risk_config`, the four seeded `core.kill_switch_state` rows, and
  the one seeded bootstrap `core.risk_config` PAPER row (`created_by =
  NULL` - see "Known gap" below).
- `0002_seed_fixture_instruments` - ~20 `provider = 'FIXTURE'` NSE equities
  in `core.instruments` (docs/schemas/instrument.md's Phase 1 note).
- `0003_table_grants` - narrows the coarse schema-level defaults from
  `ops/sql/roles_and_schemas.sql.tmpl` down to the exact per-table grants
  each docs/schemas/*.md "Security boundary" section specifies.

Run with `DATABASE_URL` (or `ATP_MIGRATION_DATABASE_URL`) pointing at the
`atp_owner` role's DSN - see `env.py`. Never against `atp_api`/
`atp_paper_exec`/`atp_worker`, which lack DDL privileges by design.

```
DATABASE_URL=postgresql+psycopg://atp_owner:...@localhost:5432/atp \
    uv run --package atp-persistence alembic -c persistence/alembic.ini upgrade head
```

`live` is created (idempotently, alongside `core`/`audit`/`paper`) by
migration 0001 but never gains a table or a grant in Phase 1 - see
`atp_persistence.models.live` and ADR-005 §5.4.

## Resolved: `core.risk_config` bootstrap (Step 6 architecture reconciliation)

docs/schemas/risk_config.md calls for "one migration-seeded PAPER config
row" with `created_by` pointing at an administrator; docs/schemas/user.md
is equally explicit that no migration ever seeds a `core.users` row.
Resolved by making `core.risk_config.created_by` nullable
(`atp_persistence.models.core.RiskConfigRow`) and seeding migration
`0001`'s bootstrap row with `created_by = NULL`, mirroring
`core.kill_switch_state.updated_by`'s existing NULL-for-system-actor
convention rather than fabricating a user row. No `core.users` row is
created by any migration. Every application-driven `risk_config` version
change must still supply a real administrator's `user_id` - see
`docs/schemas/risk_config.md`'s "Step 6 architecture reconciliation"
section.
