"""Telemetry of `POST /api/alerts` (#290): the server span, `ingest.alert`, the counter, the logs, privacy.

Built on the rigs of `test_telemetry.py` (#289): the SDK's in-memory exporters, nothing on the
network. The app is built first and telemetry set up after it, because `create_app` runs its own
`telemetry.setup` (off here), which would replace a set-up that is already running.
"""

import asyncio
import inspect
import re
from types import SimpleNamespace

import pytest
from backend_support import ANALYST_TOKEN
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import text
from test_ingest_api import ENV_NAME, HEADERS, KEY, WRONG_KEY, payload, post, raw_post
from test_ingest_limit import FakeClock, limits
from test_queue_api import AUTH
from test_telemetry import (  # noqa: F401  (rig is a fixture)
    CONTRACT,
    ON,
    MemoryMetricExporter,
    everything,
    lines,
    metric_points,
    rig,
    start,
)

from socboard import ingest, ratelimit, telemetry
from socboard.app import create_app

CANARY = dict(re.findall(r"^([a-z-]+)=(CANARY-\S+)$", CONTRACT.read_text(encoding="utf-8"), re.MULTILINE))
CALLER_TRACE, CALLER_SPAN = "0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331"
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
SERVER_SPAN = "POST /api/alerts"


@pytest.fixture
def traced(app_database, monkeypatch):
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv(ENV_NAME, KEY)
    monkeypatch.delenv(telemetry.ENDPOINT_VARIABLE, raising=False)
    app = create_app()
    spans, logs, metrics = InMemorySpanExporter(), InMemoryLogRecordExporter(), MemoryMetricExporter()
    stop, stream = start(ON, span_exporter=spans, log_exporter=logs, metric_exporter=metrics)
    yield SimpleNamespace(
        app=app, client=TestClient(app, raise_server_exceptions=False),
        spans=spans, logs=logs, metrics=metrics, stream=stream, stop=stop,
    )  # fmt: skip
    stop()


def finished(t):
    """The finished spans by name, after flushing everything."""
    t.stop()
    return {span.name: span for span in t.spans.get_finished_spans()}


def held(t):
    """Everything the exporters and the standard-output log hold, as one string."""
    t.stop()
    parts = [t.stream.getvalue()]
    parts += [span.to_json() for span in t.spans.get_finished_spans()]
    parts += [everything(record) for record in t.logs.get_finished_logs()]
    parts += [batch.to_json() for batch in t.metrics.batches]
    return "\n".join(parts)


def counts(t):
    return {point.attributes["outcome"]: point.value for _, point in metric_points(t.metrics, "socboard.ingest.alerts")}


def events(t, name):
    return [line for line in lines(t) if line["event"] == name]


def hex_trace(span):
    return trace.format_trace_id(span.context.trace_id)


# --- an accepted alert ---------------------------------------------------------------------------


def test_the_route_is_sync_so_it_runs_in_the_thread_pool(traced):
    (route,) = [r for r in traced.app.routes if getattr(r, "path", None) == "/api/alerts"]
    assert not inspect.iscoroutinefunction(route.endpoint)


def test_an_accepted_alert_has_a_span_a_count_and_a_log_with_one_trace_id(traced):
    response = post(traced.client)
    assert response.status_code == 201
    body = response.json()
    spans = finished(traced)
    assert set(spans) == {SERVER_SPAN, "ingest.alert"}
    server, child = spans[SERVER_SPAN], spans["ingest.alert"]

    assert server.kind == SpanKind.SERVER and child.kind == SpanKind.INTERNAL
    assert dict(server.attributes) == {}
    assert dict(child.attributes) == {"socboard.alert.severity": body["severity"], "socboard.case.id": body["id"]}
    assert (server.status.status_code, len(server.events), len(child.events)) == (StatusCode.UNSET, 0, 0)
    # The route ran in a worker thread and its span is still a child of the middleware's (the context was copied).
    assert child.parent.span_id == server.context.span_id
    assert child.context.trace_id == server.context.trace_id

    assert counts(traced) == {"accepted": 1}
    ((_, point),) = metric_points(traced.metrics, "socboard.ingest.alerts")
    assert [exemplar.trace_id for exemplar in point.exemplars] == [server.context.trace_id]

    (line,) = events(traced, "alert.ingested")
    assert (line["trace_id"], line["span_id"]) == (hex_trace(server), trace.format_span_id(child.context.span_id))
    (record,) = traced.logs.get_finished_logs()
    assert record.log_record.trace_id == server.context.trace_id


def test_the_response_headers_are_the_same_with_telemetry_on_and_off(traced):
    with_telemetry = post(traced.client, headers={**HEADERS, "traceparent": f"00-{CALLER_TRACE}-{CALLER_SPAN}-01"})
    traced.stop()
    without = post(traced.client)
    assert set(with_telemetry.headers) == set(without.headers)
    assert "traceparent" not in with_telemetry.headers


# --- the trace id of the caller --------------------------------------------------------------------


@pytest.mark.parametrize("flags", ["01", "00"])
def test_the_callers_trace_id_is_used_sampled_or_not(traced, flags):
    post(traced.client, headers={**HEADERS, "traceparent": f"00-{CALLER_TRACE}-{CALLER_SPAN}-{flags}"})
    spans = finished(traced)
    server, child = spans[SERVER_SPAN], spans["ingest.alert"]
    assert hex_trace(server) == hex_trace(child) == CALLER_TRACE
    assert trace.format_span_id(server.parent.span_id) == CALLER_SPAN
    assert counts(traced) == {"accepted": 1}  # an unsampled caller still gives a recorded span and count
    ((_, point),) = metric_points(traced.metrics, "socboard.ingest.alerts")
    assert [exemplar.trace_id for exemplar in point.exemplars] == [int(CALLER_TRACE, 16)]
    assert events(traced, "alert.ingested")[0]["trace_id"] == CALLER_TRACE


@pytest.mark.parametrize(
    "header",
    [None, "garbage", f"00-{CALLER_TRACE}-{CALLER_SPAN}", f"00-{'0' * 32}-{CALLER_SPAN}-01"],
    ids=["none", "garbage", "no-flags", "zero-trace-id"],
)
def test_no_or_a_malformed_traceparent_gives_a_new_trace_id(traced, header):
    headers = HEADERS if header is None else {**HEADERS, "traceparent": header}
    assert post(traced.client, headers=headers).status_code == 201
    server = finished(traced)[SERVER_SPAN]
    assert server.parent is None
    assert TRACE_ID.match(hex_trace(server)) and server.context.trace_id != 0
    assert hex_trace(server) not in (CALLER_TRACE, "0" * 32)


# --- other requests ----------------------------------------------------------------------------


def test_other_routes_get_no_span_and_no_count(traced):
    assert traced.client.get("/api/health").status_code == 200
    assert traced.client.post("/api/login", json={"username": "x", "password": "y"}).status_code == 401
    traced.client.get("/api/alerts")  # the same path with another method
    assert finished(traced) == {}
    assert counts(traced) == {}
    assert events(traced, "alert.refused") == []


# --- refused requests --------------------------------------------------------------------------


def wrong_key(t):
    return [post(t.client, payload(title=CANARY["alert-title"]), headers={"X-API-Key": CANARY["ingest-key"]})]


def invalid_body(t):
    return [raw_post(t.client, "{" + CANARY["alert-title"])]


def rate_limited(t):
    t.app.state.ingest_limiter = ratelimit.IngestLimiter(limits(1), clock=FakeClock())
    return [post(t.client, payload(title=CANARY["alert-title"])) for _ in range(2)]


@pytest.mark.parametrize(
    ("make_requests", "statuses", "accepted"),
    [(wrong_key, [401], 0), (invalid_body, [422], 0), (rate_limited, [201, 429], 1)],
    ids=["wrong-key", "invalid-body", "rate-limit"],
)
def test_a_refused_request_counts_refused_and_logs_one_fixed_event(traced, make_requests, statuses, accepted):
    assert [r.status_code for r in make_requests(traced)] == statuses
    spans = finished(traced)
    server = spans[SERVER_SPAN]
    assert dict(server.attributes) == {}
    assert counts(traced) == ({"refused": 1} | ({"accepted": accepted} if accepted else {}))
    (line,) = events(traced, "alert.refused")
    assert (line["level"], line["logger"], line["trace_id"]) == ("INFO", "socboard.events", hex_trace(server))
    assert set(line) == {"timestamp", "level", "logger", "event", "trace_id", "span_id", "service.name"}
    forbidden = [KEY, WRONG_KEY, CANARY["ingest-key"], CANARY["alert-title"]]
    assert not [value for value in forbidden if value in held(traced)]


# --- failures inside the route ----------------------------------------------------------------


def bad_sql(session, **kwargs):
    # A real database error, carrying a canary as a statement parameter (the engine hides parameters).
    session.execute(text("INSERT INTO no_such_table (a) VALUES (:a)"), {"a": CANARY["alert-title"]})


def raise_with_canaries(session, **kwargs):
    raise RuntimeError(CANARY["alert-title"], CANARY["alert-description"])


@pytest.mark.parametrize(
    ("failure", "types"),
    [(bad_sql, {"OperationalError", "ProgrammingError"}), (raise_with_canaries, {"RuntimeError"})],
    ids=["database-error", "exception"],
)
def test_a_failure_in_the_route_records_the_exception_type_only(traced, monkeypatch, failure, types):
    monkeypatch.setattr(ingest, "append_audit", failure)
    body = payload(title=CANARY["alert-title"], description=CANARY["alert-description"], source=CANARY["account-number"])
    response = post(traced.client, body)
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"

    spans = finished(traced)
    server = spans[SERVER_SPAN]
    (event,) = server.events
    assert event.name == "exception"
    assert set(event.attributes) == {"exception.type"} and event.attributes["exception.type"] in types
    assert (server.status.status_code, server.status.description) == (StatusCode.ERROR, None)
    assert len(spans["ingest.alert"].events) == 0
    assert counts(traced) == {}
    everything_held = held(traced)
    assert not [value for value in CANARY.values() if value in everything_held]
    assert events(traced, "alert.ingested") == []
    assert events(traced, "Unhandled exception")  # logged by the app, with the exception type only


# --- privacy -----------------------------------------------------------------------------------


def test_no_canary_reaches_a_span_a_metric_or_a_log(traced):
    traced.app.state.ingest_api_key = CANARY["ingest-key"]
    body = payload(
        title=CANARY["alert-title"], description=CANARY["alert-description"],
        source=CANARY["account-number"], asset=CANARY["password"],
    )  # fmt: skip
    headers = {"X-API-Key": CANARY["ingest-key"], "Authorization": f"Bearer {CANARY['token']}"}
    accepted = post(traced.client, body, headers)
    assert accepted.status_code == 201
    case_id = accepted.json()["id"]
    refused = post(traced.client, body, {"X-API-Key": CANARY["token"]})
    assert refused.status_code == 401
    # The case note goes through the cases route, as an analyst signed in with a real token.
    note = traced.client.post(f"/api/cases/{case_id}/notes", json={"text": CANARY["case-note"]}, headers=AUTH)
    assert note.status_code == 201
    login = traced.client.post("/api/login", json={"username": "test-analyst", "password": CANARY["password"]})
    assert login.status_code == 401

    spans = finished(traced)
    assert set(spans) == {SERVER_SPAN, "ingest.alert"} and len(traced.spans.get_finished_spans()) == 3
    assert counts(traced) == {"accepted": 1, "refused": 1}
    assert len(events(traced, "alert.ingested")) == len(events(traced, "alert.refused")) == 1
    exported = held(traced)
    assert not [value for value in [*CANARY.values(), ANALYST_TOKEN, KEY] if value in exported]


# --- the middleware on its own ---------------------------------------------------------------


def call(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(telemetry.IngestSpanMiddleware(app)(scope, receive, send))
    return sent


def http_scope(path="/api/alerts", method="POST", headers=()):
    return {"type": "http", "method": method, "path": path, "headers": list(headers)}


def responds(status, route=None):
    async def app(scope, receive, send):
        if route is not None:
            scope["route"] = route  # what FastAPI leaves in the scope once it has routed
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    return app


def test_the_span_is_named_from_the_route_template_after_routing(rig):
    call(responds(201, SimpleNamespace(path="/api/alerts/{template}")), http_scope())
    rig.stop()
    (span,) = rig.spans.get_finished_spans()
    assert span.name == "POST /api/alerts/{template}"


@pytest.mark.parametrize(("status", "refused"), [(201, 0), (401, 1), (422, 1), (429, 1), (500, 0)])
def test_the_status_is_read_by_wrapping_send_and_messages_pass_unchanged(rig, status, refused):
    sent = call(responds(status), http_scope())
    assert [m["type"] for m in sent] == ["http.response.start", "http.response.body"] and sent[0]["status"] == status
    rig.stop()
    points = metric_points(rig.metrics, "socboard.ingest.alerts")
    assert sum(point.value for _, point in points) == refused
    assert len(events_of(rig, "alert.refused")) == refused


def events_of(rig, name):
    return [line for line in lines(rig) if line["event"] == name]


def test_an_exception_is_recorded_by_type_only_and_re_raised(rig):
    async def failing(scope, receive, send):
        raise ValueError(CANARY["alert-title"])

    with pytest.raises(ValueError, match=CANARY["alert-title"]):
        call(failing, http_scope())
    rig.stop()
    (span,) = rig.spans.get_finished_spans()
    (event,) = span.events
    assert (event.name, dict(event.attributes)) == ("exception", {"exception.type": "ValueError"})
    assert (span.status.status_code, span.status.description) == (StatusCode.ERROR, None)
    assert CANARY["alert-title"] not in span.to_json()


@pytest.mark.parametrize(
    "scope",
    [
        {"type": "lifespan"},
        {"type": "websocket", "path": "/api/alerts", "headers": []},
        http_scope(method="GET"),
        http_scope(path="/api/alerts/extra"),
        http_scope(path="/api/health"),
    ],
    ids=["lifespan", "websocket", "other-method", "other-path-1", "other-path-2"],
)
def test_other_scopes_go_straight_to_the_app_and_get_no_span(rig, scope):
    seen = []

    async def app(*args):
        seen.append(args)

    async def receive():
        return {}

    async def send(message):
        return None

    asyncio.run(telemetry.IngestSpanMiddleware(app)(scope, receive, send))
    assert seen == [(scope, receive, send)] and seen[0][2] is send
    rig.stop()
    assert rig.spans.get_finished_spans() == ()


def test_with_telemetry_off_the_request_goes_to_the_app_untouched():
    assert not telemetry.is_on()
    seen = []

    async def app(*args):
        seen.append(args)

    async def receive():
        return {}

    async def send(message):
        return None

    scope = http_scope()
    asyncio.run(telemetry.IngestSpanMiddleware(app)(scope, receive, send))
    assert len(seen) == 1 and seen[0][0] is scope and seen[0][1] is receive and seen[0][2] is send
