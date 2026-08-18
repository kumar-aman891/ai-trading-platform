# Integration tests

`db/` (Phase 1 Step 5) requires a live PostgreSQL/Redis instance bootstrapped
by `ops/sql/roles_and_schemas.sql.tmpl` - see `docker-compose.test.yml` and
`ops/docker/test.env.example`.

Two modes, controlled by `ATP_REQUIRE_INTEGRATION_STACK` (Phase 1 Step 11):

- Unset (default): every fixture in `db/conftest.py` skips (never fails,
  never fakes a pass) when the required `TEST_*` environment variable is
  unset or the instance is unreachable, so `pytest` stays safe to run with
  no Docker installed at all.
- `ATP_REQUIRE_INTEGRATION_STACK=1`: the same conditions call `pytest.fail`
  instead. Use this whenever the stack is expected to be present (CI, or
  `make test-integration`/`ops/scripts/run_integration_tests.sh`), so a
  broken or unreachable stack is reported as a failure rather than as 77
  quiet skips.

To actually run this suite locally, run pytest **inside** the compose
network via the `test-runner` service - a host-side `pytest` process cannot
reach `postgres`/`redis` at all, since `atp_test_internal` is
`internal: true` and neither service publishes a host port:

```
make test-integration
```

which is equivalent to:

```
docker compose -f docker-compose.test.yml --env-file ops/docker/test.env.example \
    up --wait postgres redis
ATP_REQUIRE_INTEGRATION_STACK=1 \
    docker compose -f docker-compose.test.yml --env-file ops/docker/test.env.example \
    run --rm -e ATP_REQUIRE_INTEGRATION_STACK test-runner
docker compose -f docker-compose.test.yml --env-file ops/docker/test.env.example down -v
```

`db/test_stack_lifecycle.py` drives `docker compose` itself (config
validation, startup, health, shutdown) and is opt-in
(`RUN_DOCKER_LIFECYCLE_TESTS=1`) rather than autodetected.

Repository implementations (Phase 1 Step 8) will add further integration
tests here once there is real persistence code to exercise against these
roles/schemas.
