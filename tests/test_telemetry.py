"""Telemetry core (#289): off by default, resource, JSON logs, scrubbing, OTLP logs, flush.

Every test that turns telemetry on uses the SDK's in-memory exporters (no network). The providers
live in `socboard.telemetry` and are never installed process-wide, so nothing leaks between tests;
the `rig` fixture shuts its set-up down at the end of each test.
"""

import io
import json
import logging
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend_support import TEST_TOKEN_SECRET, sqlite_url
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, LogRecordExporter
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from socboard import telemetry
from socboard.app import create_app

CONTRACT = Path(__file__).resolve().parent.parent / "observability" / "CONTRACT.md"
CANARIES = re.findall(r"^[a-z-]+=(CANARY-\S+)$", CONTRACT.read_text(encoding="utf-8"), re.MULTILINE)
ON = {
    telemetry.ENDPOINT_VARIABLE: "http://collector.invalid:4318",
    telemetry.RESOURCE_VARIABLE: "deployment.environment.name=ci,service.version=1.2.3",
}
TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID = re.compile(r"^[0-9a-f]{16}$")


class MemoryMetricExporter(MetricExporter):
    def __init__(self):
        super().__init__()
        self.batches = []

    def export(self, metrics_data, timeout_millis=10_000, **kwargs):
        self.batches.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis=10_000):
        return True

    def shutdown(self, timeout_millis=30_000, **kwargs):
        pass


class FailingLogExporter(LogRecordExporter):
    """Fails every export, and remembers the bodies it was asked to send."""

    def __init__(self):
        self.bodies = []

    def export(self, batch):
        self.bodies.extend(item.log_record.body for item in batch)
        raise RuntimeError("collector down")

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30_000):
        return True


class ThreadLogExporter(LogRecordExporter):
    """Remembers the thread of each export call and the bodies it was asked to send."""

    def __init__(self):
        self.threads = []
        self.bodies = []

    def export(self, batch):
        self.threads.append(threading.get_ident())
        self.bodies.extend(item.log_record.body for item in batch)

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30_000):
        return True


def start(environ, **exporters):
    stream = io.StringIO()
    stop = telemetry.setup(environ, stream=stream, **exporters)
    return stop, stream


@pytest.fixture
def rig():
    spans, logs, metrics = InMemorySpanExporter(), InMemoryLogRecordExporter(), MemoryMetricExporter()
    stop, stream = start(ON, span_exporter=spans, log_exporter=logs, metric_exporter=metrics)
    yield SimpleNamespace(spans=spans, logs=logs, metrics=metrics, stream=stream, stop=stop)
    stop()


def lines(rig):
    return [json.loads(line) for line in rig.stream.getvalue().splitlines()]


def metric_points(exporter, name):
    return [
        (metric, point)
        for data in exporter.batches
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def everything(record):
    return repr((record.log_record.body, dict(record.log_record.attributes), record.log_record.severity_text))


# --- off by default ----------------------------------------------------------------------------


def test_the_canary_list_of_the_contract_has_seven_values():
    assert len(CANARIES) == 7


@pytest.mark.parametrize("value", [None, "", "  "])
def test_off_creates_no_provider_exporter_handler_or_thread(value, monkeypatch, caplog):
    def refuse(*args, **kwargs):
        raise AssertionError("created while telemetry is off")

    for name in ("TracerProvider", "MeterProvider", "LoggerProvider", "BatchSpanProcessor", "_OtlpLogHandler"):
        monkeypatch.setattr(telemetry, name, refuse)
    environ = {} if value is None else {telemetry.ENDPOINT_VARIABLE: value}
    threads, handlers = threading.enumerate(), list(logging.getLogger("socboard").handlers)
    caplog.set_level(logging.DEBUG)

    stop = telemetry.setup(environ)
    with telemetry.start_span("ingest.alert", attributes={telemetry.SEVERITY_ATTRIBUTE: "High"}) as span:
        telemetry.count_ingest("accepted")
        telemetry.record_time_to_queue(1.0)
        telemetry.log_event("alert.ingested")
    stop()

    assert span is None
    assert not telemetry.is_on()
    assert threading.enumerate() == threads
    assert logging.getLogger("socboard").handlers == handlers
    assert not [record for record in caplog.records if record.name == telemetry.EVENT_LOGGER]


def test_the_app_builds_with_telemetry_off():
    app = create_app({"SOCBOARD_TOKEN_SECRET": TEST_TOKEN_SECRET})
    assert not telemetry.is_on()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200


# --- resource ----------------------------------------------------------------------------------


def test_the_resource_has_the_service_name_and_the_two_operator_attributes(rig):
    with telemetry.start_span("ingest.alert"):
        telemetry.count_ingest("accepted")
        logging.getLogger("socboard.ingest").warning("Ingest refused")
    rig.stop()
    expected = {"service.name": "socboard", "deployment.environment.name": "ci", "service.version": "1.2.3"}
    assert dict(rig.spans.get_finished_spans()[0].resource.attributes) == expected
    assert dict(rig.logs.get_finished_logs()[0].resource.attributes) == expected
    assert dict(rig.metrics.batches[-1].resource_metrics[0].resource.attributes) == expected


def test_a_missing_operator_attribute_is_reported_once_and_does_not_stop_the_app():
    stop, stream = start({telemetry.ENDPOINT_VARIABLE: ON[telemetry.ENDPOINT_VARIABLE], telemetry.RESOURCE_VARIABLE: "service.version=9"},
                         span_exporter=InMemorySpanExporter(), log_exporter=InMemoryLogRecordExporter(), metric_exporter=MemoryMetricExporter())  # fmt: skip
    try:
        reports = [json.loads(line) for line in stream.getvalue().splitlines()]
        assert len(reports) == 1
        assert reports[0]["level"] == "WARNING"
        assert reports[0]["logger"] == "socboard.telemetry"
        assert "deployment.environment.name" in reports[0]["event"]
        assert "service.version" not in reports[0]["event"]
        assert telemetry.is_on()
    finally:
        stop()


def test_no_report_when_both_are_set(rig):
    assert rig.stream.getvalue() == ""


# --- the limit on the two operator-set values (#312) --------------------------------------------


def run_with_resource(raw):
    """Set up with `raw` as the resource variable; return the resource attributes, the JSON lines and the exported logs."""
    spans, logs = InMemorySpanExporter(), InMemoryLogRecordExporter()
    environ = {telemetry.ENDPOINT_VARIABLE: ON[telemetry.ENDPOINT_VARIABLE], telemetry.RESOURCE_VARIABLE: raw}
    stop, stream = start(environ, span_exporter=spans, log_exporter=logs, metric_exporter=MemoryMetricExporter())
    with telemetry.start_span("ingest.alert"):
        pass
    stop()
    resource = dict(spans.get_finished_spans()[0].resource.attributes)
    return resource, stream.getvalue(), "".join(everything(record) for record in logs.get_finished_logs())


@pytest.mark.parametrize("name", telemetry.OPERATOR_ATTRIBUTES)
@pytest.mark.parametrize("value", ["dev", "ci", "1.4.0+abc1234", "a" * 64, "A-b_9.+", "1.4.0%2Babc"])
def test_an_operator_value_in_the_limit_is_used_unchanged(name, value):
    resource, output, _ = run_with_resource(f"{name}={value}")
    assert resource[name] == value.replace("%2B", "+")
    assert output.count(name) == 0  # the one report names only the other attribute


@pytest.mark.parametrize("name", telemetry.OPERATOR_ATTRIBUTES)
@pytest.mark.parametrize(
    "value",
    ["", "a b", "a/b", "a<b", 'a"b', "a'b", "a\x01b", "a\tb", "caf\u00e9", "a" * 65, "a%20b", "a%0Ab", "a%C3%A9", "a%2Fb"],
    ids=["empty", "space", "slash", "less-than", "double-quote", "single-quote", "control", "tab", "non-ascii", "65-characters",
         "encoded-space", "encoded-newline", "encoded-non-ascii", "encoded-slash"],
)  # fmt: skip
def test_a_refused_operator_value_is_treated_as_missing_and_reported_once_without_the_value(name, value):
    other = next(n for n in telemetry.OPERATOR_ATTRIBUTES if n != name)
    resource, output, exported = run_with_resource(f"{name}={value},{other}=ok")
    assert name not in resource
    assert resource[other] == "ok"
    reports = [json.loads(line) for line in output.splitlines()]
    assert len(reports) == 1
    assert reports[0]["level"] == "WARNING"
    assert name in reports[0]["event"]
    assert other not in reports[0]["event"]


def test_a_refused_value_is_absent_from_every_log_output():
    marker = "REFUSED-VALUE/<script>"
    resource, output, exported = run_with_resource(f"deployment.environment.name={marker},service.version=1.0")
    assert "deployment.environment.name" not in resource
    assert "deployment.environment.name" in output
    assert "REFUSED-VALUE" not in output + exported
    assert "script" not in output + exported


def test_both_refused_values_are_named_in_the_one_report():
    resource, output, _ = run_with_resource("deployment.environment.name=a b,service.version=" + "z" * 65)
    assert resource == {"service.name": "socboard"}
    reports = [json.loads(line) for line in output.splitlines()]
    assert len(reports) == 1
    assert "deployment.environment.name" in reports[0]["event"]
    assert "service.version" in reports[0]["event"]
    assert "zzzz" not in output


def test_a_refused_value_does_not_stop_the_app():
    stop, _ = start({**ON, telemetry.RESOURCE_VARIABLE: "service.version=a b"}, span_exporter=InMemorySpanExporter(),
                    log_exporter=InMemoryLogRecordExporter(), metric_exporter=MemoryMetricExporter())  # fmt: skip
    try:
        assert telemetry.is_on()
    finally:
        stop()


# --- JSON lines --------------------------------------------------------------------------------


def test_a_line_inside_a_span_has_the_span_ids_and_the_contract_fields(rig):
    with telemetry.start_span("ingest.alert") as span:
        telemetry.log_event("alert.ingested")
    context = span.get_span_context()
    (line,) = lines(rig)
    assert list(line) == ["timestamp", "level", "logger", "event", "trace_id", "span_id", "service.name"]
    assert line["trace_id"] == trace.format_trace_id(context.trace_id)
    assert line["span_id"] == trace.format_span_id(context.span_id)
    assert TRACE_ID.match(line["trace_id"]) and SPAN_ID.match(line["span_id"])
    assert (line["level"], line["logger"], line["event"], line["service.name"]) == (
        "INFO",
        "socboard.events",
        "alert.ingested",
        "socboard",
    )
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line["timestamp"])


def test_a_line_outside_a_span_has_no_trace_fields(rig):
    telemetry.log_event("alert.first_in_queue")
    (line,) = lines(rig)
    assert list(line) == ["timestamp", "level", "logger", "event", "service.name"]


@pytest.mark.parametrize("name", ["socboard.auth", "socboard.errors", "socboard.ingest"])
def test_the_existing_loggers_emit_json_with_their_message_text_unchanged(rig, name):
    logging.getLogger(name).warning("Login refused")
    (line,) = lines(rig)
    assert (line["logger"], line["event"], line["level"]) == (name, "Login refused", "WARNING")


def test_a_critical_record_is_an_error_line(rig):
    logging.getLogger("socboard.errors").critical("Unhandled exception")
    assert lines(rig)[0]["level"] == "ERROR"


def test_the_info_events_are_not_hidden_by_the_root_level(rig):
    logging.getLogger("socboard.auth").info("Login refused")
    assert len(lines(rig)) == 1


def test_the_logger_level_is_restored_at_shutdown():
    before = logging.getLogger("socboard").level
    stop, _ = start(ON, span_exporter=InMemorySpanExporter(), log_exporter=InMemoryLogRecordExporter(), metric_exporter=MemoryMetricExporter())  # fmt: skip
    stop()
    assert logging.getLogger("socboard").level == before


# --- scrubbing ---------------------------------------------------------------------------------


def test_canaries_as_arguments_extras_and_exception_messages_reach_neither_output(rig):
    logger = logging.getLogger("socboard.errors")
    with telemetry.start_span("ingest.alert") as span:
        try:
            raise ValueError(*CANARIES)
        except ValueError:
            logger.error("Unhandled %s", *CANARIES[:1], extra={"title": CANARIES[1]}, exc_info=True)
        logger.error("Rendered %s %s", CANARIES[2], {"note": CANARIES[3]}, exc_info=ValueError(CANARIES[4]))
    rig.stop()

    output = rig.stream.getvalue()
    assert not [canary for canary in CANARIES if canary in output]
    first, second = lines(rig)
    assert (first["event"], second["event"]) == ("Unhandled %s", "Rendered %s %s")
    assert first["trace_id"] == second["trace_id"] == trace.format_trace_id(span.get_span_context().trace_id)

    exported = rig.logs.get_finished_logs()
    assert len(exported) == 2
    for record in exported:
        assert not [canary for canary in CANARIES if canary in everything(record)]
        assert record.log_record.attributes["exception.type"] == "ValueError"
        assert "exception.message" not in record.log_record.attributes
        assert "exception.stacktrace" not in record.log_record.attributes
        assert "title" not in record.log_record.attributes
        assert record.log_record.trace_id == span.get_span_context().trace_id
    assert [record.log_record.body for record in exported] == ["Unhandled %s", "Rendered %s %s"]


def test_the_original_record_is_left_as_it_was(rig, caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("socboard.auth").info("Login succeeded for user id %d", 7)
    assert caplog.records[0].getMessage() == "Login succeeded for user id 7"


def test_an_exception_text_is_not_recorded_on_a_span(rig):
    with pytest.raises(ValueError):
        with telemetry.start_span("ingest.alert"):
            raise ValueError(CANARIES[0])
    rig.stop()
    (span,) = rig.spans.get_finished_spans()
    assert len(span.events) == 0
    assert span.status.status_code == StatusCode.UNSET
    assert span.status.description is None
    assert CANARIES[0] not in repr(span.to_json())


# --- logs over OTLP ----------------------------------------------------------------------------


def test_handlers_are_on_the_socboard_logger_only(rig):
    def telemetry_handlers(logger):
        return [h for h in logger.handlers if isinstance(h.formatter, telemetry.JsonFormatter) or type(h).__name__ == "_OtlpLogHandler"]

    app_logger = logging.getLogger("socboard")
    assert len(telemetry_handlers(app_logger)) == 2
    assert telemetry_handlers(logging.getLogger()) == []
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if name.startswith("opentelemetry") and isinstance(logger, logging.Logger):
            assert telemetry_handlers(logger) == []
    rig.stop()
    assert telemetry_handlers(app_logger) == []


def test_the_sdks_own_warning_is_not_sent_through_the_otlp_handler():
    failing = FailingLogExporter()
    stop, _ = start(ON, span_exporter=InMemorySpanExporter(), log_exporter=failing, metric_exporter=MemoryMetricExporter())
    sdk_logger = logging.getLogger("opentelemetry.sdk._shared_internal")
    sdk_logger.warning("sdk trouble")
    sdk_logger.exception("Exception while exporting Logs.")
    logging.getLogger("socboard.ingest").warning("Ingest refused %s", CANARIES[0])
    stop()  # the flush runs the failing export, which the SDK logs

    assert failing.bodies == ["Ingest refused %s"]


# --- unreachable collector ---------------------------------------------------------------------


def test_an_unroutable_endpoint_does_not_slow_create_app_or_a_call(monkeypatch, tmp_path):
    monkeypatch.setattr(telemetry, "EXPORT_TIMEOUT_SECONDS", 0.2)
    environ = {
        "SOCBOARD_TOKEN_SECRET": TEST_TOKEN_SECRET,
        "SOCBOARD_DATABASE_URL": sqlite_url(tmp_path / "x.db"),
        telemetry.ENDPOINT_VARIABLE: "http://192.0.2.1:4318",  # a documentation address (RFC 5737): never a real host
    }
    started = time.monotonic()
    app = create_app(environ)
    with TestClient(app) as client:
        with telemetry.start_span("ingest.alert"):
            telemetry.count_ingest("accepted")
            logging.getLogger("socboard.ingest").warning("Ingest refused")
        response = client.get("/api/health")
        elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed < 1.0  # the exit of the block (the flush) is bounded by the 0.2 second timeouts and is not timed
    assert not telemetry.is_on()


def test_a_log_call_exports_nothing_and_the_export_runs_in_a_background_thread(monkeypatch):
    # The batching processor reads its delay from the environment when it is built. A long delay means
    # its worker does not wake during the test, so the only export is the one that shutdown forces.
    monkeypatch.setenv("OTEL_BLRP_SCHEDULE_DELAY", "600000")
    exporter = ThreadLogExporter()
    stop, _ = start(ON, span_exporter=InMemorySpanExporter(), log_exporter=exporter, metric_exporter=MemoryMetricExporter())
    try:
        logging.getLogger("socboard.ingest").warning("Ingest refused")
        # SimpleLogRecordProcessor.on_emit calls the exporter in the calling thread, so it would fail here.
        assert exporter.threads == []
    finally:
        stop()  # shutdown joins the worker thread, which exports what is queued

    assert exporter.bodies == ["Ingest refused"]
    assert len(exporter.threads) == 1
    assert exporter.threads[0] != threading.get_ident()


# --- sampler and spans -------------------------------------------------------------------------


def test_an_unsampled_traceparent_does_not_drop_the_span_and_the_trace_id_is_the_callers(rig):
    caller_trace, caller_span = "0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331"
    with telemetry.start_span("POST /api/alerts", headers={"traceparent": f"00-{caller_trace}-{caller_span}-00"}) as span:
        assert span.is_recording()
    rig.stop()
    (finished,) = rig.spans.get_finished_spans()
    assert trace.format_trace_id(finished.context.trace_id) == caller_trace
    assert trace.format_span_id(finished.parent.span_id) == caller_span
    assert finished.kind == trace.SpanKind.SERVER


def test_a_span_with_no_traceparent_starts_a_trace(rig):
    with telemetry.start_span("POST /api/alerts", headers={}):
        pass
    rig.stop()
    (finished,) = rig.spans.get_finished_spans()
    assert finished.parent is None


def test_the_contract_spans_nest_and_the_queue_span_links_back(rig):
    with telemetry.start_span("POST /api/alerts", headers={}) as server:
        with telemetry.start_span(
            "ingest.alert", attributes={telemetry.SEVERITY_ATTRIBUTE: "Critical", telemetry.CASE_ID_ATTRIBUTE: "case-1"}
        ) as ingest:
            link = ingest.get_span_context()
    with telemetry.start_span("queue.first_seen", links=[link], attributes={telemetry.CASE_ID_ATTRIBUTE: "case-1"}):
        pass
    rig.stop()
    by_name = {s.name: s for s in rig.spans.get_finished_spans()}
    assert by_name["ingest.alert"].parent.span_id == server.get_span_context().span_id
    assert by_name["ingest.alert"].kind == trace.SpanKind.INTERNAL
    assert dict(by_name["ingest.alert"].attributes) == {"socboard.alert.severity": "Critical", "socboard.case.id": "case-1"}
    queue = by_name["queue.first_seen"]
    assert queue.parent is None
    assert queue.context.trace_id != by_name["ingest.alert"].context.trace_id
    assert [(item.context.trace_id, item.context.span_id) for item in queue.links] == [(link.trace_id, link.span_id)]


@pytest.mark.parametrize(
    ("name", "attributes", "error"),
    [
        ("ingest.alert", {"http.url": "x"}, ValueError),
        ("ingest.alert", {"socboard.alert.severity": "Urgent"}, ValueError),
        ("ingest.alert", {"socboard.case.id": 5}, ValueError),
        ("made.up", {}, KeyError),
    ],
)
def test_a_span_outside_the_contract_is_refused(rig, name, attributes, error):
    with pytest.raises(error):
        with telemetry.start_span(name, attributes=attributes):
            pass
    rig.stop()
    assert rig.spans.get_finished_spans() == ()


# --- metrics and events ------------------------------------------------------------------------


def test_the_two_metrics_use_the_contract_names_unit_labels_and_buckets(rig):
    telemetry.count_ingest("accepted")
    telemetry.count_ingest("accepted")
    telemetry.count_ingest("refused")
    telemetry.record_time_to_queue(2.5)
    rig.stop()

    points = metric_points(rig.metrics, "socboard.ingest.alerts")
    assert points[0][0].unit == "{alert}"
    assert {point.attributes["outcome"]: point.value for _, point in points} == {"accepted": 2, "refused": 1}
    ((metric, point),) = metric_points(rig.metrics, "socboard.alert.time_to_queue")
    assert metric.unit == "s"
    assert tuple(point.explicit_bounds) == (0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 30)
    assert (point.count, point.sum) == (1, 2.5)
    assert dict(point.attributes) == {}


def test_a_value_recorded_just_before_shutdown_is_in_the_in_memory_exporters(rig):
    with telemetry.start_span("ingest.alert"):
        telemetry.count_ingest("accepted")
        telemetry.log_event("alert.ingested")
    assert rig.spans.get_finished_spans() == ()  # batched: not exported yet
    rig.stop()
    assert len(rig.spans.get_finished_spans()) == 1
    assert len(rig.logs.get_finished_logs()) == 1
    assert metric_points(rig.metrics, "socboard.ingest.alerts")[0][1].value == 1
    assert not telemetry.is_on()


@pytest.mark.parametrize("call", [lambda: telemetry.count_ingest("maybe"), lambda: telemetry.log_event("made.up")])
def test_a_label_or_event_outside_the_contract_is_refused(rig, call):
    with pytest.raises(ValueError):
        call()


def test_a_second_setup_replaces_the_first_and_the_old_stop_does_nothing():
    first, _ = start(ON, span_exporter=InMemorySpanExporter(), log_exporter=InMemoryLogRecordExporter(), metric_exporter=MemoryMetricExporter())  # fmt: skip
    second, _ = start(ON, span_exporter=InMemorySpanExporter(), log_exporter=InMemoryLogRecordExporter(), metric_exporter=MemoryMetricExporter())  # fmt: skip
    try:
        first()
        assert telemetry.is_on()
        assert len(logging.getLogger("socboard").handlers) == 2
    finally:
        second()
    assert not telemetry.is_on()


# --- the app ----------------------------------------------------------------------------------


def test_create_app_sets_up_with_its_environment_and_the_lifespan_shuts_down(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(telemetry, "setup", lambda environ: calls.append(("setup", dict(environ))) or (lambda: calls.append(("stop",))))
    environ = {"SOCBOARD_TOKEN_SECRET": TEST_TOKEN_SECRET, "SOCBOARD_DATABASE_URL": sqlite_url(tmp_path / "y.db")}
    app = create_app(environ)
    assert calls == [("setup", environ)]
    with TestClient(app):
        assert len(calls) == 1
    assert calls[-1] == ("stop",)
    assert len(calls) == 2
