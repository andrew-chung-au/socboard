"""Tests for observability/emitter.py (#340), against a fake OTLP collector on the loopback.

No Docker. The fake collector is the standard library's `http.server`; it keeps the request bodies, and
the tests decode them with the OTLP protobuf classes the exporter itself uses. Every value is synthetic.
"""

import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "observability" / "emitter.py"
spec = importlib.util.spec_from_file_location("observability_emitter", SCRIPT)
emitter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(emitter)

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


class Received(list):
    answers: list  # statuses to answer with, once each; 200 after that


@pytest.fixture
def collector():
    received = Received()
    answers = received.answers = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers.get("Content-Length") or 0))))
            self.send_response(answers.pop(0) if answers else 200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True).start()
    collector_url = f"http://127.0.0.1:{server.server_address[1]}"
    yield collector_url, received
    server.shutdown()
    server.server_close()


def attributes(items):
    return {a.key: a.value.string_value for a in items}


def test_it_sends_one_span_one_log_record_and_one_histogram_value_under_the_trace_id(collector, capsys):
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    endpoint, received = collector
    assert emitter.main(["--trace-id", TRACE_ID, "--endpoint", endpoint]) == 0
    assert sorted(path for path, _ in received) == ["/v1/logs", "/v1/metrics", "/v1/traces"]
    body = {path: data for path, data in received}

    spans = ExportTraceServiceRequest.FromString(body["/v1/traces"]).resource_spans
    assert attributes(spans[0].resource.attributes)["service.name"] == "socboard"
    span = spans[0].scope_spans[0].spans[0]
    assert span.name == "ingest.alert" and span.trace_id.hex() == TRACE_ID
    assert attributes(span.attributes) == {"socboard.alert.severity": "High", "socboard.case.id": "1"}  # nothing else
    assert len(spans) == 1 and len(spans[0].scope_spans[0].spans) == 1

    record = ExportLogsServiceRequest.FromString(body["/v1/logs"]).resource_logs[0].scope_logs[0].log_records[0]
    assert attributes(record.attributes) == {"event": "alert.ingested"}  # nothing else
    assert record.trace_id.hex() == TRACE_ID and record.span_id == span.span_id

    metric = ExportMetricsServiceRequest.FromString(body["/v1/metrics"]).resource_metrics[0].scope_metrics[0].metrics[0]
    assert (metric.name, metric.unit) == ("socboard.alert.time_to_queue", "s")
    point = metric.histogram.data_points[0]
    assert point.count == 1 and tuple(point.explicit_bounds) == (0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 30)
    assert [(e.trace_id.hex(), e.span_id) for e in point.exemplars] == [(TRACE_ID, span.span_id)]
    assert not point.attributes  # no label: the exemplar is the only link to a trace
    assert capsys.readouterr().out.strip() == f"emitter: sent trace {TRACE_ID} to {endpoint}"


def test_the_resource_is_socboard_whatever_the_environment_says(collector, monkeypatch):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    monkeypatch.setenv("OTEL_SERVICE_NAME", "other")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.name=other,deployment.environment.name=ci,service.version=abc123")
    endpoint, received = collector
    assert emitter.main(["--trace-id", TRACE_ID, "--endpoint", endpoint]) == 0
    resource = ExportTraceServiceRequest.FromString(dict(received)["/v1/traces"]).resource_spans[0].resource
    assert attributes(resource.attributes)["service.name"] == "socboard"
    assert attributes(resource.attributes)["deployment.environment.name"] == "ci"  # operator-set, as the contract says


@pytest.mark.parametrize("trace_id", ["", "abc", "0" * 32, "4BF92F3577B34DA6A3CE929D0E0E4736", TRACE_ID + "0", "g" * 32])
def test_a_bad_trace_id_exits_2_before_anything_is_sent(collector, trace_id, capsys):
    endpoint, received = collector
    assert emitter.main(["--trace-id", trace_id, "--endpoint", endpoint]) == 2
    assert received == []
    assert "refused" in capsys.readouterr().err


@pytest.mark.parametrize(
    "endpoint",
    ["https://127.0.0.1:4318", "http://example.com:4318", "http://127.0.0.1:4318/v1/traces", "http://user@127.0.0.1:4318", "http://10.0.0.5:4318", "http://127.0.0.1:4318?x=1", "http://[::1"],
)
def test_an_endpoint_that_is_not_the_loopback_over_plain_http_is_refused(endpoint, capsys):
    assert emitter.main(["--trace-id", TRACE_ID, "--endpoint", endpoint]) == 2
    assert "refused" in capsys.readouterr().err


def test_the_default_endpoint_is_the_loopback_collector_port():
    assert emitter.check_endpoint(emitter.DEFAULT_ENDPOINT) == "http://127.0.0.1:4318"
    assert emitter.check_endpoint("http://localhost:4318/") == "http://localhost:4318"


def test_a_collector_that_refuses_a_signal_makes_the_emitter_exit_1_and_not_say_sent(collector, capsys):
    endpoint, received = collector
    received.answers.extend([400, 400, 400])  # a refusal is not retried by the exporter
    assert emitter.main(["--trace-id", TRACE_ID, "--endpoint", endpoint]) == 1
    captured = capsys.readouterr()
    assert "sent trace" not in captured.out and "export problem" in captured.err


def test_the_canary_option_puts_one_extra_attribute_on_every_signal(collector):
    from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    endpoint, received = collector
    assert emitter.main(["--trace-id", TRACE_ID, "--endpoint", endpoint, "--canary", "CANARY-7f3a91c2d04e5b68"]) == 0
    body = dict(received)
    spans = ExportTraceServiceRequest.FromString(body["/v1/traces"]).resource_spans[0]
    span = spans.scope_spans[0].spans[0]
    assert attributes(span.attributes) == {"socboard.alert.severity": "High", "socboard.case.id": "1", "socboard.canary": "CANARY-7f3a91c2d04e5b68"}
    assert attributes(spans.resource.attributes)["socboard.canary"] == "CANARY-7f3a91c2d04e5b68"
    record = ExportLogsServiceRequest.FromString(body["/v1/logs"]).resource_logs[0].scope_logs[0].log_records[0]
    assert attributes(record.attributes) == {"event": "alert.ingested", "socboard.canary": "CANARY-7f3a91c2d04e5b68"}
    point = ExportMetricsServiceRequest.FromString(body["/v1/metrics"]).resource_metrics[0].scope_metrics[0].metrics[0].histogram.data_points[0]
    assert attributes(point.attributes) == {"socboard.canary": "CANARY-7f3a91c2d04e5b68"}


def test_without_the_canary_option_nothing_extra_is_sent(collector):
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    endpoint, received = collector
    assert emitter.main(["--trace-id", TRACE_ID, "--endpoint", endpoint]) == 0
    spans = ExportTraceServiceRequest.FromString(dict(received)["/v1/traces"]).resource_spans[0]
    assert "socboard.canary" not in attributes(spans.resource.attributes) and "socboard.canary" not in attributes(spans.scope_spans[0].spans[0].attributes)


@pytest.mark.parametrize("canary", ["", "plain", "CANARY-", "CANARY-" + "x" * 61, "CANARY-a b", "canary-abc"])
def test_a_bad_canary_exits_2_before_anything_is_sent(collector, canary, capsys):
    endpoint, received = collector
    assert emitter.main(["--trace-id", TRACE_ID, "--endpoint", endpoint, "--canary", canary]) == 2
    assert received == [] and "refused" in capsys.readouterr().err
