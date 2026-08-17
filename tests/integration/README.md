# Integration tests

`db/` (Phase 1 Step 5) requires a live PostgreSQL/Redis instance bootstrapped
by `ops/sql/roles_and_schemas.sql.tmpl` - see `docker-compose.test.yml` and
`ops/docker/test.env.example`. Every fixture in `db/conftest.py` skips
(never fails, never fakes a pass) when the required `TEST_*` environment
variable is unset or the instance is unreachable, so `pytest` stays safe to
run with no Docker installed at all.

To actually run this suite locally:

```
docker compose -f docker-compose.test.yml --env-file ops/docker/test.env.example \
    up --wait postgres redis
export $(grep -v '^#' ops/docker/test.env.example | xargs)
uv run pytest tests/integration/db -v
docker compose -f docker-compose.test.yml --env-file ops/docker/test.env.example down -v
```

`db/test_stack_lifecycle.py` drives `docker compose` itself (config
validation, startup, health, shutdown) and is opt-in
(`RUN_DOCKER_LIFECYCLE_TESTS=1`) rather than autodetected.

Repository implementations (Phase 1 Step 8) will add further integration
tests here once there is real persistence code to exercise against these
roles/schemas.
