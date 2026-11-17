#!/usr/bin/env bash
# The one entry point of the observability checks (#340, extended by #292). The `socboard observability`
# workflow runs this script and nothing else, so a later check changes this file, not the workflow.
#
# It starts the full layered stack (the app, Postgres and the proxy of compose.yaml, with the overlay of
# compose.observability.yaml: collector, Tempo, Loki, Prometheus, Alertmanager and Grafana), waits for readiness,
# sends one trace id from the emitter with a canary attribute the collector must drop, looks for the trace
# id in Tempo, Loki and Prometheus, checks that the canary is in none of them, prints the memory each
# container uses, and always stops the stack. Exit 0 only when every check passed.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

compose() {
  docker compose --env-file compose.secrets --env-file observability.secrets -f compose.yaml -f compose.observability.yaml "$@"
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
# Grafana's admin password, in its own file (git-ignored, never printed).
uv run --locked python scripts/observability_secrets.py

echo "--- checking what Compose makes of the overlay (ports on 127.0.0.1 only, the stack network, the app's exporter)"
compose config --format json | uv run --locked python observability/ci/rendered.py overlay
# Without the overlay in the command, the stack is what it was.
docker compose --env-file compose.secrets -f compose.yaml config --format json | uv run --locked python observability/ci/rendered.py base

echo "--- starting the stack"
compose up --build --detach --wait --wait-timeout 300

echo "--- waiting for readiness"
uv run --locked python observability/ci/query.py ready

echo "--- memory used by each container (the six observability services stay within 1.5 GiB, #292)"
docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}'

TRACE_ID="$(uv run --locked python -c 'import secrets; print(secrets.token_hex(16))')"
CANARY="CANARY-$(uv run --locked python -c 'import secrets; print(secrets.token_hex(8))')"
echo "--- trace id: $TRACE_ID"
uv run --locked python observability/emitter.py --trace-id "$TRACE_ID" --canary "$CANARY"

echo "--- looking for the trace id, and for the canary that must not be there"
uv run --locked python observability/ci/query.py absent "$TRACE_ID" "$CANARY"

# Reviewer evidence (#293): with SCREENSHOT_DIR set (the workflow sets it where it also has a browser and
# uploads the directory as an artifact), the real probe runs against the real app for about 40 seconds,
# so the dashboard has points to draw, and Grafana's dashboard, one trace and its log lines are
# photographed. Left unset, as it is on a developer's machine, this is skipped.
if [ -n "${SCREENSHOT_DIR:-}" ]; then
  echo "--- probe cycles for the screenshots (the app on 127.0.0.1:8000)"
  FROM_MS="$(( $(date +%s) * 1000 - 60000 ))"
  PROBE_LOG="$(mktemp)"
  (
    set -a
    . ./compose.secrets
    set +a
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 \
    OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=local,service.version=unknown \
    OTEL_METRIC_EXPORT_INTERVAL=5000 \
      uv run --locked python scripts/observability_probe.py http://127.0.0.1:8000 --count 8 --interval 5
  ) | tee "$PROBE_LOG" || echo "the probe exited non-zero (a cycle was slow or failed); its lines are above, and the screenshots go on"
  PROBE_TRACE="$(grep -o 'trace=[0-9a-f]\{32\}' "$PROBE_LOG" | tail -n 1 | cut -d= -f2)"
  test -n "$PROBE_TRACE" || { echo "no probe cycle printed a trace id"; exit 1; }
  echo "--- the probe's last trace id: $PROBE_TRACE (a few seconds for the export to land)"
  sleep 15
  uv run --locked python observability/ci/query.py find "$PROBE_TRACE"
  TO_MS="$(( $(date +%s) * 1000 + 60000 ))"
  node observability/screenshots/capture.mjs --out "$SCREENSHOT_DIR" --trace-id "$PROBE_TRACE" --from "$FROM_MS" --to "$TO_MS"
  printf '{"trace_id": "%s", "from_ms": %s, "to_ms": %s}\n' "$PROBE_TRACE" "$FROM_MS" "$TO_MS" > "$SCREENSHOT_DIR/window.json"
  ls -l "$SCREENSHOT_DIR"
fi
