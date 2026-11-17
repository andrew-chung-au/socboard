#!/usr/bin/env bash
# Job 1 of the on-call drill (#299): plant the defect, start the layered stack, run the probe until
# QueueLatencyOverSLO fires, then write the incident bundle and photograph the firing alert.
#
#   bash observability/drill/drill.sh <out folder>
#
# Run in a checkout of `main` on a CI runner (Docker, uv, Node and Playwright's Chromium installed, as in
# the `socboard observability` workflow). The defect (observability/drill/defect.patch) is applied to this
# checkout only; nothing is committed. Writes into <out folder>:
#   bundle/         incident.json, p95.json, exemplars.json, trace-<id>.json (observability/drill/bundle.py)
#   evidence/       alert.png (Alertmanager), dashboard.png (Grafana), from the screenshot script
#   handoff_exit    the hand-off's exit code: 0 the alert fired, 3 it did not within the timeout, other = failure
# The script exits 0 when the hand-off ran to its end, whatever its code (the workflow decides what each
# code means, with observability/drill/summary.py); it exits non-zero only when the set-up failed.
# The model key is not available here, and the stack is always stopped.
set -euo pipefail

OUT="${1:?usage: drill.sh <out folder>}"
ENVIRONMENT="ci"
HANDOFF_TIMEOUT=600  # seconds: the issue's "within 10 minutes"

cd "$(dirname "${BASH_SOURCE[0]}")/../.."
mkdir -p "$OUT/bundle" "$OUT/evidence"
OUT="$(cd "$OUT" && pwd)"
VERSION="$(git rev-parse --short=12 HEAD)"

compose() {
  docker compose --env-file compose.secrets --env-file observability.secrets -f compose.yaml -f compose.observability.yaml "$@"
}

PROBE_LOOP_PID=""
stop() {
  status=$?
  echo "--- stopping the stack"
  if [ -n "$PROBE_LOOP_PID" ]; then kill "$PROBE_LOOP_PID" 2>/dev/null || true; fi
  compose logs --no-color --tail=40 || true
  compose down --volumes --remove-orphans || true
  exit "$status"
}
trap stop EXIT

echo "--- planting the defect (on this checkout only, never committed)"
git apply --check observability/drill/defect.patch
git apply observability/drill/defect.patch
git diff --stat

uv sync --locked
uv run --locked python scripts/compose_secrets.py
uv run --locked python scripts/observability_secrets.py

echo "--- starting the stack"
compose up --build --detach --wait --wait-timeout 300
uv run --locked python observability/ci/query.py ready

echo "--- starting the probe: one batch of 5 cycles after another, so each batch exports its histogram"
(
  set -a
  . ./compose.secrets
  set +a
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
  export OTEL_RESOURCE_ATTRIBUTES="deployment.environment.name=${ENVIRONMENT},service.version=${VERSION}"
  export OTEL_METRIC_EXPORT_INTERVAL=5000
  while true; do
    # A slow cycle makes the probe exit 1 (that is the defect); the loop goes on.
    uv run --locked python scripts/observability_probe.py http://127.0.0.1:8000 --count 5 --interval 5 || true
  done
) &
PROBE_LOOP_PID=$!

echo "--- waiting up to ${HANDOFF_TIMEOUT} seconds for QueueLatencyOverSLO"
handoff=0
python3 observability/oncall/handoff.py http://127.0.0.1:9093 --grafana-url http://localhost:3000 \
  --environment "$ENVIRONMENT" --version "$VERSION" --timeout "$HANDOFF_TIMEOUT" --out "$OUT/incident.json" || handoff=$?
echo "$handoff" > "$OUT/handoff_exit"
echo "the hand-off exited with $handoff"

if [ "$handoff" -eq 0 ]; then
  echo "--- the incident bundle"
  python3 observability/drill/bundle.py --incident "$OUT/incident.json" --environment "$ENVIRONMENT" --version "$VERSION" --out "$OUT/bundle"

  echo "--- photographing the firing alert (a failure here does not fail the drill; the evidence record notes it)"
  node observability/drill/alert_shot.mjs --out "$OUT/evidence" || echo "the screenshot failed; the drill goes on"
fi
exit 0
