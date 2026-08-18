#!/bin/sh
# Brings up the ephemeral test stack (docker-compose.test.yml), runs the
# Step 5 database/Redis security suite against it *inside* the compose
# network via the `test-runner` service, and tears the stack down again
# regardless of outcome. See tests/integration/README.md.
#
# Runs pytest inside `atp_test_internal` deliberately, not on the host: that
# network is `internal: true` and neither `postgres` nor `redis` publishes a
# host port (see docker-compose.test.yml), so a host-side `pytest` process
# can never actually reach either service - every fixture in
# tests/integration/db/conftest.py would (previously did) silently skip on
# the resulting connection failure, making this script report success while
# testing nothing. ATP_REQUIRE_INTEGRATION_STACK=1 additionally converts
# that skip into a hard failure, so a future regression of this kind is
# caught rather than silently passing again.
set -eu

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

COMPOSE_FILE="docker-compose.test.yml"
ENV_FILE="ops/docker/test.env.example"

cleanup() {
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" down -v
}
trap cleanup EXIT

docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up --wait postgres redis

ATP_REQUIRE_INTEGRATION_STACK=1 \
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" run --rm \
    -e ATP_REQUIRE_INTEGRATION_STACK \
    test-runner
