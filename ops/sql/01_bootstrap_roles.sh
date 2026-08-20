#!/bin/sh
# Runs automatically by the postgres:16 image's docker-entrypoint-initdb.d
# mechanism on first container init (empty data directory only - see
# docker-compose.yml / docker-compose.test.yml, which mount ops/sql/ read-only
# at /docker-entrypoint-initdb.d).
#
# This wrapper exists (rather than a plain .sql file in this directory) so
# role passwords can be passed in as psql variables from the environment
# instead of being hard-coded into a tracked file (security/SECRET_HANDLING.md,
# CLAUDE.md rule #4). roles_and_schemas.sql.tmpl is named .sql.tmpl, not
# .sql, precisely so the entrypoint's own auto-scanner does not also try to
# run it directly (which would fail - it references :'variables' that only
# this script supplies).
set -eu

: "${POSTGRES_USER:?POSTGRES_USER must be set}"
: "${POSTGRES_DB:?POSTGRES_DB must be set}"
: "${ATP_OWNER_PASSWORD:?ATP_OWNER_PASSWORD must be set}"
: "${ATP_API_PASSWORD:?ATP_API_PASSWORD must be set}"
: "${ATP_PAPER_EXEC_PASSWORD:?ATP_PAPER_EXEC_PASSWORD must be set}"
: "${ATP_WORKER_PASSWORD:?ATP_WORKER_PASSWORD must be set}"
: "${ATP_STRATEGY_PASSWORD:?ATP_STRATEGY_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    -v atp_database="$POSTGRES_DB" \
    -v atp_owner_password="$ATP_OWNER_PASSWORD" \
    -v atp_api_password="$ATP_API_PASSWORD" \
    -v atp_paper_exec_password="$ATP_PAPER_EXEC_PASSWORD" \
    -v atp_worker_password="$ATP_WORKER_PASSWORD" \
    -v atp_strategy_password="$ATP_STRATEGY_PASSWORD" \
    -f "$(dirname "$0")/roles_and_schemas.sql.tmpl"
