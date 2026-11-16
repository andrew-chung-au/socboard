# Roadmap 4: DevOps (observability, alerting, on-call)

Order and evidence only. Acceptance criteria live on the issues; open or closed is read from the tracker. Scope: `_docs/specs.md` section 4 (cross-cutting story), section 5 (the 5-second invariant) and section 6 (Phase 3). Method: `_docs/planning/04_devops.md`. Dev and prod separation, promotion and rollback belong to the Deploy lanes (#266 to #269) and are not here. Lane and file ownership: the DevOps row of `_docs/planning/deploy-coordination.md` (#282).

- **Status:** filed 2026-11-16 as #285 to #301 (O0 to O16), groomed, waiting for the human's labels.
- **Goal:** One journey, an alert ingested until it is in the queue within 5 seconds, emits a metric, a trace and structured logs that share one trace id, with no customer data or credential in any signal. The signals are collected, stored and viewable in a UI that starts with one make command. One alert fires on users missing the 5-second promise. An on-call agent wakes on that alert and opens the smallest fix as a pull request. It never merges and never pushes to `main`.

## Decisions (human, 2026-11-16; recorded by #287)

- **Backend:** self-hosted in `compose.observability.yaml`, layered on `compose.yaml` (never edited): OpenTelemetry Collector, Tempo, Loki, Prometheus, Alertmanager and Grafana, each pinned. No account, runs in CI. The all-in-one `grafana/otel-lgtm` image is the fallback, with the human's agreement first.
- **Runner:** `anthropics/claude-code-action` with its key held only in the `oncall` Environment. For the drill the pull request is opened with the workflow's own `GITHUB_TOKEN`, so it gets no automatic CI; the run log shows the tests the agent ran.
- **Alert path:** a script in the drill workflow reads Alertmanager's API and starts the agent when `QueueLatencyOverSLO` is firing. A webhook to `repository_dispatch` is the later dev path and reuses the script's parsing.

## Design in short

- The app exports OTLP to a collector, which routes to Tempo (traces), Loki (logs) and Prometheus (metrics, with exemplars). Spans, metrics and logs are written by hand; there is no SQL or HTTP auto-instrumentation, because those can carry alert text.
- The queue side of the journey is measured in process: ingest remembers the case and its trace context, and the first queue read that returns the case records `time_to_queue` linked to the ingest trace. One worker is assumed, as for the app's limiters.
- The alert is a Prometheus rule so `promtool` can test it offline: p95 of `time_to_queue` over 5 minutes above 5 s for 2 minutes.
- The proof of the shared trace id chooses the id itself, sends it as `traceparent`, and reads Tempo, Loki and Prometheus back. A canary test plants fake alert text, a note, a password, a key, a token and an account number and fails if any is stored.
- The drill runs in one workflow on an ephemeral stack, with a defect patch applied to a throwaway branch, so `main` never holds the defect.

## Tasks, in order (O-labels), tracks and evidence

Tracks run in parallel after the contract and the human sitting: **A** app telemetry, **B** stack, **C** alert and agent. Each issue's evidence is linked from the README's Observability section by #300, which hands the Planning lane the lines for the reviewer section.

| Issue | Track | Needs | Evidence a reviewer can open |
|---|---|---|---|
| #285 O0 Signal contract | Contract | none | merged PR, `observability/CONTRACT.md` |
| #286 O1 Spec detail (protected diff) | Human | #285 | merged PR |
| #287 O2 Decision record | Human | none | the decision record |
| #288 O3 OpenTelemetry dependencies (protected) | A | the human's approval | PR and CI run |
| #289 O4 Telemetry core | A | #285, #288 | CI run (unit tests) |
| #290 O5 Instrument ingest, canary test | A | #289 | CI run |
| #291 O6 Instrument the queue read | A | #289, #290 | CI run |
| #292 O7 Collector and storage | B | #285, #287 | CI run of the overlay check |
| #293 O8 Grafana and `make observability-up` | B | #292 | committed screenshots, CI run |
| #294 O9 Synthetic probe | C | #285 | CI run |
| #295 O10 CI check: shared trace id, no canary (Heavy) | Join | #290, #292, #294 | the CI run log |
| #296 O11 Alert and promtool tests | C | #285, #292 | CI run of promtool |
| #297 O12 Alert-to-agent hand-off | C | #285, #296 | CI run |
| #298 O13 On-call mandate and guards (Heavy) | C | #287, #297 | CI run of the guard tests, mandate file |
| #299 O14 On-call drill (Heavy) | Join | #291, #293, #294 to #298 | the drill run log and the agent's pull request |
| #300 O15 README Observability section and link check | Docs | #299 | README, link check |
| #301 O16 Show it on dev | Later | #300, first deploy (#237) | screenshots and a dated record |

Critical path: O3, O4, O5, O10, O14. Everything else fits beside it.

## Waves

- **Wave 0 (one sitting with the human):** approve the decisions record, the spec diff (#286), the dependencies (#288), and one combined Makefile and `AGENTS.md` diff (#293); label #285 to #288; write the `oncall` Environment steps (#298).
- **Wave 1 (parallel):** A: #288 then #289. B: #292. C: #294 and #296.
- **Wave 2:** A: #290, #291. B: #293. C: #297, #298.
- **Wave 3:** #295, then #299, then #300.
- **Waits for the dev deployment:** #301 only. It needs the first deploy, and a server with room for the stack (a `t3.small` with 2 GiB also carries two app stacks); it may end as a record that it does not fit.

## Workflow and protected-file diffs

Workflows, the Makefile, `AGENTS.md`, `pyproject.toml`, `uv.lock` and `_docs/specs.md` are protected. The DevOps lane gives exact diffs: the CI steps of #292, #296 and #295, the drill workflow of #299, the Makefile targets of #293, the dependencies of #288 and the spec text of #286. The human batches them in one sitting.

## Not in this phase

Dev and prod separation, promotion and rollback (#266 to #269); managed telemetry; automatic merge of the agent's pull request; alerts other than the one on queue latency; a recording (`.kiro/steering/evidence-and-steps.md`, item 4).

## Archive when

The drill's run log and the agent's pull request are linked from the README, the CI run of the shared-trace-id check passes on `main`, and the promtool tests pass on `main`.
