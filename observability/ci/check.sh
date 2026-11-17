#!/usr/bin/env bash
# The one entry point of the observability checks (#340, extended by #292). The `socboard observability`
# workflow runs this script and nothing else, so a later check changes this file, not the workflow.
#
# It starts the full layered stack (the app, Postgres and the proxy of compose.yaml, with the overlay of
# compose.observability.yaml: collector, Tempo, Loki, Prometheus and Alertmanager), waits for readiness,
# sends one trace id from the emitter with a canary attribute the collector must drop, looks for the trace
# id in Tempo, Loki and Prometheus, checks that the canary is in none of them, prints the memory each
# container uses, and always stops the stack. Exit 0 only when every check passed.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

compose() {
  docker compose --env-file compose.secrets -f compose.yaml -f compose.observability.yaml "$@"
}

stop() {
  status=$?
  echo "--- stopping the stack"
  compose logs --no-color --tail=40 || true
  compose down --volumes --remove-orphans || true
  exit "$status"
}
trap stop EXIT

uv sync --locked

# Throwaway local secrets for the app's stack (git-ignored, never printed), as `make compose-up` makes them.
uv run --locked python scripts/compose_secrets.py

echo "--- starting the stack"
compose up --build --detach --wait --wait-timeout 300

echo "--- waiting for readiness"
uv run --locked python observability/ci/query.py ready

echo "--- memory used by each container (the six services stay within 1.5 GiB, #292)"
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'

TRACE_ID="$(uv run --locked python -c 'import secrets; print(secrets.token_hex(16))')"
CANARY="CANARY-$(uv run --locked python -c 'import secrets; print(secrets.token_hex(8))')"
echo "--- trace id: $TRACE_ID"
uv run --locked python observability/emitter.py --trace-id "$TRACE_ID" --canary "$CANARY"

echo "--- looking for the trace id, and for the canary that must not be there"
uv run --locked python observability/ci/query.py absent "$TRACE_ID" "$CANARY"
