#!/bin/sh
# Brings up the ephemeral test stack (docker-compose.test.yml), runs the
# Step 5 database/Redis security suite against it, and tears the stack down
# again regardless of outcome. See tests/integration/README.md.
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

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

uv run pytest tests/integration/db -v
