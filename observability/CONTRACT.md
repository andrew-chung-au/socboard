# Observability signal contract

The names the observability work shares (roadmap 4, `_docs/planning/roadmap-4-devops.md`). The app's telemetry (#289 to #291), the collector and storage (#292, #293), the probe and the checks (#294, #295) and the alert and on-call agent (#296 to #299) each build and test against this file. A change to a name here is a change to this file first, in its own pull request.

Rules that apply to every table:

- A value derived from a request is a fixed word, a number, an id or a version, never free text, so alert and case text cannot reach a signal. The values not derived from a request are `deployment.environment.name` and `service.version`, which an operator sets (section 1) and no request can reach.
- The allowed labels and fields are the whole list. Anything not listed is refused.
- `socboard.case.id` is the public case id exactly as the API returns it, an opaque string (`openapi.yaml`), never a database key shown in another form.

## 1. Resource attributes

Set once on the telemetry resource, so they appear on every metric, span and log.

| Name | Type | Value | Set from | Implemented by |
|---|---|---|---|---|
| `service.name` | string | `socboard` | fixed in code | #289 |
| `deployment.environment.name` | string | for example `dev`, `ci` | `OTEL_RESOURCE_ATTRIBUTES` | #289 |
| `service.version` | string | the image or commit version | `OTEL_RESOURCE_ATTRIBUTES` | #289 |

- `deployment.environment.name` and `service.version` are operator-set: read from `OTEL_RESOURCE_ATTRIBUTES` at start-up, never from a request, so alert and case text cannot reach them.
- No new application variable is added for these.
- Telemetry is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Unset or empty means nothing is created or exported.

## 2. Metrics

| Name | Type | Unit | Labels (allowed values) | Prometheus name after the collector | Implemented by |
|---|---|---|---|---|---|
| `socboard.ingest.alerts` | counter | `{alert}` | `outcome` (`accepted`, `refused`) | `socboard_ingest_alerts_total` | #290 |
| `socboard.alert.time_to_queue` | histogram | `s` | none other than the resource attributes | `socboard_alert_time_to_queue_seconds_bucket` (also `_sum`, `_count`) | #291 |

- `socboard.alert.time_to_queue` has explicit bucket bounds, in seconds: `0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 30`.
- The histogram measures the time from the accepted ingest to the first queue read that returns the case.
- Exemplars carry the ingest trace id (section 5). The metric has no other label, so the exemplar is the only link from a bucket to a trace.
- `refused` counts ingest requests the API refuses (bad key, bad body, limit). It records no reason text.

## 3. Spans

| Span name | Kind | Parent | Link | Allowed attributes | Implemented by |
|---|---|---|---|---|---|
| the server span of `POST /api/alerts` | server | the caller's `traceparent`, if any | none | none (the name is the fixed text `POST /api/alerts`) | #290 |
| `ingest.alert` | internal | the server span above | none | `socboard.alert.severity`, `socboard.case.id` | #290 |
| `queue.first_seen` | internal | none (its own trace) | a link to the span context of `ingest.alert` | `socboard.alert.severity`, `socboard.case.id` | #291 |

| Attribute | Type | Allowed values |
|---|---|---|
| `socboard.alert.severity` | string | `Low`, `Medium`, `High`, `Critical` (the severities of `openapi.yaml`) |
| `socboard.case.id` | string | the public case id exactly as the API returns it, an opaque string (`openapi.yaml`) |

- No SQL, HTTP-client or other auto-instrumentation is used. Spans are written by hand.
- The only attributes any span may carry are the two above. No URL, header, query string or HTTP status attribute is set.
- No span carries an event or status message built from request text.

## 4. Logs

One JSON object per line, on standard output.

| Field | Type | Value |
|---|---|---|
| `timestamp` | string | UTC time, RFC 3339 |
| `level` | string | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logger` | string | the logger's name, a fixed dotted name in the code |
| `event` | string | one of the events below |
| `trace_id` | string | 32 lower-case hex characters (section 5); left out of the line when no span is active |
| `span_id` | string | 16 lower-case hex characters; left out of the line when no span is active |
| `service.name` | string | `socboard` |

| Event | When | Extra fields | Implemented by |
|---|---|---|---|
| `alert.ingested` | the ingest accepted an alert | none | #290 |
| `alert.first_in_queue` | the first queue read returned the case | none | #291 |

- `event` is a fixed word from this table, never built from alert text.
- A log line has at most these seven fields. `trace_id` and `span_id` are absent, not zero or empty, when no span is active. The case id and severity are found through the trace id.

## 5. Trace id format

- 32 lower-case hex characters: the trace id of the W3C `traceparent` header (`00-<trace id>-<span id>-<flags>`).
- It is written the same way in a span, in the `trace_id` log field and in a metric exemplar. The all-zero id is never emitted.

## 6. Alert

A Prometheus rule, so `promtool` can test it offline (#296).

| Part | Value |
|---|---|
| Name | `QueueLatencyOverSLO` |
| Meaning of the expression | the 95th percentile of `socboard_alert_time_to_queue_seconds_bucket`, over the last 5 minutes, is above 5 seconds |
| `for` | `2m` |
| Label `severity` | `page` |
| Label `service` | `socboard` |
| Annotations | `summary`, `environment`, `version`, `owner`, `dashboard_url` |

| Annotation | Content | Source |
|---|---|---|
| `summary` | one fixed sentence saying the 5-second promise is missed | written in the rule |
| `environment` | the `deployment.environment.name` value | the series' resource attributes |
| `version` | the `service.version` value | the series' resource attributes |
| `owner` | the team or person to page | written in the rule |
| `dashboard_url` | a link to the Grafana dashboard | written in the rule |

- The 5 seconds is N of `_docs/specs.md` section 5 (N = 5s), the product SLO. The 2 minutes is the proposal of #285, confirmed by the human with the issue.

## 7. Alertmanager payload

The hand-off script (#297) reads these fields and no others.

| Field | Meaning |
|---|---|
| `status` | `firing` or `resolved` |
| `alerts[].labels.alertname` | the alert's name, matched against `QueueLatencyOverSLO` |
| `alerts[].annotations` | the five annotations of section 6 |
| `alerts[].startsAt` | when the alert began firing, RFC 3339 |

## 8. Forbidden in any signal

No metric, span attribute, span event, log field, exemplar or resource attribute holds:

- the alert title or description
- case notes
- passwords
- the ingest key
- tokens (user bearer tokens and any other token)
- account numbers

The canary strings below are fake values, one per kind. The privacy tests (#290, #295) plant each one in a request and fail if any appears in what Tempo, Loki or Prometheus stored. The block is the only list; a test reads it from here.

```text
alert-title=CANARY-TITLE-7f3a91c2
alert-description=CANARY-DESCRIPTION-b04e5d68
case-note=CANARY-NOTE-2d8c6e17
password=CANARY-PASSWORD-91a7f3e5
ingest-key=CANARY-INGEST-KEY-c63b0d29
token=CANARY-TOKEN-5e1a8f74
account-number=CANARY-ACCOUNT-4096127
```

Each line is `<kind>=<value>`. The value is what a test searches for.

## 9. Check of the refused case

Every row above was read for a value that could hold alert or case text. The values derived from a request are fixed words (`outcome`, severities, `event`, `level`), numbers, versions, ids or fixed sentences written in the code or the rule. None of them is free text.

The exceptions are `deployment.environment.name` and `service.version`, and the section 6 annotations `environment` and `version`, which carry those two values. An operator sets them in `OTEL_RESOURCE_ATTRIBUTES` at start-up (section 1). No request can reach them, so alert and case text cannot enter a signal through them.
