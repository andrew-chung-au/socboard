#!/usr/bin/env bash
# The one entry point of the observability checks (#340). The `socboard observability` workflow runs
# this script and nothing else, so a later check changes this file, not the workflow.
#
# It starts the skeleton stack, waits for readiness, sends one trace id from the emitter, looks for it in
# Tempo, Loki and Prometheus, and always stops the stack. The log shows the trace id and the result of
# each store. Exit 0 only when all three found it.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
SKELETON=observability/ci/skeleton/compose.yaml

stop() {
  status=$?
  echo "--- stopping the stack"
  docker compose -f "$SKELETON" logs --no-color --tail=40 || true
  docker compose -f "$SKELETON" down --volumes --remove-orphans || true
  exit "$status"
}
trap stop EXIT

uv sync --locked

echo "--- starting the stack"
docker compose -f "$SKELETON" up --detach

echo "--- waiting for readiness"
uv run --locked python observability/ci/query.py ready

TRACE_ID="$(uv run --locked python -c 'import secrets; print(secrets.token_hex(16))')"
echo "--- trace id: $TRACE_ID"
uv run --locked python observability/emitter.py --trace-id "$TRACE_ID"

echo "--- looking for the trace id"
uv run --locked python observability/ci/query.py find "$TRACE_ID"
