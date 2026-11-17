"""Telemetry of the first queue read of a case (#291): `queue.first_seen`, `alert.first_in_queue`, `time_to_queue`.

Built on the rigs of `test_telemetry.py` and `test_telemetry_ingest.py`: the SDK's in-memory
exporters, nothing on the network, and a fake monotonic clock (`FakeClock`, passed to
`telemetry.setup`), so no test sleeps.
"""

import threading
from types import SimpleNamespace

import pytest
from backend_support import LEAD_TOKEN
from fastapi.testclient import TestClient
from opentelemetry import trace
from test_ingest_api import ENV_NAME, KEY, payload, post
from test_ingest_limit import FakeClock
from test_queue_api import AUTH, add_case
from test_telemetry import (  # noqa: F401  (rig is a fixture)
    ON,
    MemoryMetricExporter,
    metric_points,
    rig,
    start,
)
from test_telemetry_ingest import CANARY, events, finished, hex_trace, held

from socboard import telemetry
from socboard.app import create_app

HISTOGRAM = "socboard.alert.time_to_queue"
LEAD = {"Authorization": f"Bearer {LEAD_TOKEN}"}


@pytest.fixture
def queued(app_database, monkeypatch):
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv(ENV_NAME, KEY)
    monkeypatch.delenv(telemetry.ENDPOINT_VARIABLE, raising=False)
    app = create_app()
    clock = FakeClock()
    spans, logs, metrics = InMemorySpanExporter(), InMemoryLogRecordExporter(), MemoryMetricExporter()
    stop, stream = start(ON, span_exporter=spans, log_exporter=logs, metric_exporter=metrics, clock=clock)
    yield SimpleNamespace(
        app=app, client=TestClient(app, raise_server_exceptions=False), clock=clock, url=app_database, runtime=telemetry._runtime,
        spans=spans, logs=logs, metrics=metrics, stream=stream, stop=stop,
    )  # fmt: skip
    stop()


def read(t, path="/api/queue", headers=AUTH):
    response = t.client.get(path, headers=headers)
    assert response.status_code == 200
    return response


def ingest(t, **changes):
    response = post(t.client, payload(**changes))
    assert response.status_code == 201
    return response.json()["id"]


def recorded(t):
    """The histogram's data points, after flushing everything."""
    t.stop()
    return [point for _, point in metric_points(t.metrics, HISTOGRAM)]


def first_seen(t):
    t.stop()
    return [span for span in t.spans.get_finished_spans() if span.name == "queue.first_seen"]


def ids(response):
    return [case["id"] for case in response.json()["cases"]]


# --- the accepted case ---------------------------------------------------------------------------


def test_a_watched_first_read_records_the_delay_once_with_the_ingest_trace(queued):
    read(queued)
    queued.clock.now += 2
    case_id = ingest(queued)
    queued.clock.now += 3
    assert case_id in ids(read(queued))

    ((point,)) = recorded(queued)
    assert (point.count, point.sum) == (1, 3.0)
    ingest_span = finished(queued)["ingest.alert"]
    (seen,) = first_seen(queued)
    # The exemplar is the ingest's trace, not the read's own (queue.first_seen is the active span at `record`).
    assert [e.trace_id for e in point.exemplars] == [ingest_span.context.trace_id]
    assert seen.context.trace_id != ingest_span.context.trace_id

    assert seen.parent is None and seen.kind == trace.SpanKind.INTERNAL
    assert dict(seen.attributes) == {"socboard.alert.severity": "Low", "socboard.case.id": case_id}
    (link,) = seen.links
    assert (link.context.trace_id, link.context.span_id) == (ingest_span.context.trace_id, ingest_span.context.span_id)
    assert (len(seen.events), len(seen.links)) == (0, 1)

    (line,) = events(queued, "alert.first_in_queue")
    assert line["trace_id"] == hex_trace(ingest_span)
    assert set(line) == {"timestamp", "level", "logger", "event", "trace_id", "span_id", "service.name"}
    assert not queued.runtime.pending


def test_a_second_read_records_nothing(queued):
    read(queued)
    queued.clock.now += 1
    ingest(queued)
    queued.clock.now += 1
    read(queued)
    queued.clock.now += 1
    read(queued)
    queued.clock.now += 1
    read(queued)
    assert [p.count for p in recorded(queued)] == [1]
    assert len(first_seen(queued)) == 1
    assert len(events(queued, "alert.first_in_queue")) == 1


# --- watched only --------------------------------------------------------------------------------


def test_an_idle_board_gets_the_span_and_the_event_but_no_value(queued):
    case_id = ingest(queued)
    queued.clock.now += 40
    assert case_id in ids(read(queued))
    assert recorded(queued) == []
    assert len(first_seen(queued)) == 1
    assert len(events(queued, "alert.first_in_queue")) == 1


@pytest.mark.parametrize(
    ("gap_before_ingest", "expected"),
    [(0, [1.0]), (5.9, [1.0]), (6.0, [1.0]), (6.1, [])],
    ids=["same-instant", "inside", "boundary", "outside"],
)
def test_a_read_within_6_seconds_before_the_ingest_counts_as_watching(queued, gap_before_ingest, expected):
    read(queued)
    queued.clock.now += gap_before_ingest
    ingest(queued)
    queued.clock.now += 1
    read(queued)
    assert [p.sum for p in recorded(queued)] == expected
    assert len(first_seen(queued)) == 1


def test_a_read_after_the_ingest_counts_as_watching_even_when_none_came_before(queued):
    case_id = ingest(queued)  # no queue read at all before the ingest
    queued.clock.now += 1
    with telemetry._runtime.lock:  # a read that does not return the case: the case is held back from the map
        held_back = telemetry._runtime.pending.pop(case_id)
    read(queued)
    queued.clock.now += 20
    with telemetry._runtime.lock:
        telemetry._runtime.pending[case_id] = held_back
    assert case_id in ids(read(queued))
    assert [p.sum for p in recorded(queued)] == [21.0]


# --- restart and the other reads -----------------------------------------------------------------


def test_open_cases_already_in_the_database_record_nothing(queued):
    add_case(queued.url, 5001, "High")
    add_case(queued.url, 5002)
    read(queued)
    queued.clock.now += 1
    assert len(ids(read(queued))) == 2
    assert recorded(queued) == []
    assert first_seen(queued) == []
    assert events(queued, "alert.first_in_queue") == []


def test_a_case_ingested_before_telemetry_started_is_never_recorded(app_database, monkeypatch):
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setenv(ENV_NAME, KEY)
    monkeypatch.delenv(telemetry.ENDPOINT_VARIABLE, raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert post(client).status_code == 201  # ingested by the process "before the restart"
    metrics = MemoryMetricExporter()
    stop, _ = start(ON, span_exporter=InMemorySpanExporter(), log_exporter=InMemoryLogRecordExporter(), metric_exporter=metrics)
    try:
        assert len(client.get("/api/queue", headers=AUTH).json()["cases"]) == 1
    finally:
        stop()
    assert metric_points(metrics, HISTOGRAM) == []


def test_other_reads_do_not_count_as_appearing_in_the_queue(queued):
    read(queued)
    case_id = ingest(queued)
    queued.clock.now += 1
    read(queued, f"/api/cases/{case_id}")
    read(queued, f"/api/cases/{case_id}/audit", LEAD)
    read(queued, "/api/cases?status=Closed", LEAD)
    assert list(telemetry._runtime.pending) == [case_id]
    assert recorded(queued) == [] and first_seen(queued) == []
    assert events(queued, "alert.first_in_queue") == []


def test_other_reads_do_not_make_the_queue_watched(queued):
    read(queued, "/api/cases?status=Closed", LEAD)
    case_id = ingest(queued)
    queued.clock.now += 1
    read(queued, f"/api/cases/{case_id}")
    queued.clock.now += 1
    read(queued)
    assert recorded(queued) == []  # the first queue read ever: nobody was watching it before


# --- bounds --------------------------------------------------------------------------------------


def remember(case_id):
    with telemetry.start_span("ingest.alert") as span:
        telemetry.remember_ingest(case_id, span)


def test_the_map_keeps_the_newest_entries_up_to_its_bound(queued, monkeypatch):
    monkeypatch.setattr(telemetry, "PENDING_MAX", 3)
    for number in range(5):
        remember(f"C-{number}")
        queued.clock.now += 1
    assert list(telemetry._runtime.pending) == ["C-2", "C-3", "C-4"]


def test_the_bound_is_10_000_and_the_age_is_10_minutes():
    assert (telemetry.PENDING_MAX, telemetry.PENDING_MAX_AGE_SECONDS) == (10_000, 600.0)


def test_an_old_entry_is_dropped_without_a_value_or_an_event(queued):
    remember("C-old")
    queued.clock.now += 601
    remember("C-new")
    assert list(telemetry._runtime.pending) == ["C-new"]
    queued.clock.now += 601
    read(queued)  # drops the new one too
    assert not telemetry._runtime.pending
    assert recorded(queued) == []  # nothing recorded for the age at expiry
    assert events(queued, "alert.first_in_queue") == []


def test_an_entry_exactly_10_minutes_old_is_still_kept(queued):
    remember("C-1")
    queued.clock.now += 600
    remember("C-2")
    assert list(telemetry._runtime.pending) == ["C-1", "C-2"]


# --- privacy -------------------------------------------------------------------------------------


def test_the_map_holds_an_id_a_time_and_a_span_context_only_and_no_canary_is_exported(queued):
    read(queued)
    body = {
        "title": CANARY["alert-title"], "description": CANARY["alert-description"],
        "source": CANARY["account-number"], "asset": CANARY["password"],
    }  # fmt: skip
    case_id = ingest(queued, **body)
    ((entry_time, context),) = telemetry._runtime.pending.values()
    assert list(telemetry._runtime.pending) == [case_id]
    assert isinstance(entry_time, float) and isinstance(context, trace.SpanContext)
    assert not [value for value in CANARY.values() if value in repr(telemetry._runtime.pending)]
    queued.clock.now += 1
    assert case_id in ids(read(queued))
    exported = held(queued)
    assert not [value for value in [*CANARY.values(), KEY] if value in exported]
    assert len(first_seen(queued)) == 1


# --- simultaneous reads --------------------------------------------------------------------------


def test_simultaneous_reads_record_the_case_once(rig):
    telemetry._runtime.clock = FakeClock()
    telemetry.queue_read([])  # someone is watching
    with telemetry.start_span("ingest.alert") as span:
        telemetry.remember_ingest("C-1", span)
    case = SimpleNamespace(id="C-1", severity="High")
    start_together = threading.Barrier(8)

    def go():
        start_together.wait()
        telemetry.queue_read([case])

    threads = [threading.Thread(target=go) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    rig.stop()
    assert [p.count for _, p in metric_points(rig.metrics, HISTOGRAM)] == [1]
    assert len([s for s in rig.spans.get_finished_spans() if s.name == "queue.first_seen"]) == 1


# --- off, and the queue unchanged ----------------------------------------------------------------


def test_with_telemetry_off_no_map_is_kept_and_the_queue_is_the_same(app_database, monkeypatch):
    monkeypatch.setenv(ENV_NAME, KEY)
    monkeypatch.delenv(telemetry.ENDPOINT_VARIABLE, raising=False)
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert not telemetry.is_on()
    one = post(client).json()["id"]
    before = client.get("/api/queue", headers=AUTH)
    assert telemetry._runtime is None  # nowhere to hold an entry
    metrics = MemoryMetricExporter()
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    stop, _ = start(ON, span_exporter=InMemorySpanExporter(), log_exporter=InMemoryLogRecordExporter(), metric_exporter=metrics)
    try:
        assert not telemetry._runtime.pending  # the ingest while off left nothing
    finally:
        stop()
    assert ids(before) == [one]


def test_the_queue_response_is_the_same_with_telemetry_on_and_off(queued):
    case_id = ingest(queued)
    on = read(queued).json()
    queued.stop()
    off = read(queued).json()
    assert on["cases"] == off["cases"] and [c["id"] for c in on["cases"]] == [case_id]
