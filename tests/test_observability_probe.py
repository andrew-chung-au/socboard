"""Tests for scripts/observability_probe.py (#294, the HTTP side), against a fake app.

The fake app is the standard library's `http.server` on 127.0.0.1 with a free port: no Docker, no
OpenTelemetry. The measurement goes to a fake recorder, and the clock and the sleep are fakes, so a
30-second wait takes no time. Every value here is synthetic.
"""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from socboard import smoke

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "observability_probe.py"
spec = importlib.util.spec_from_file_location("observability_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

PASSWORD = "Zq7-synthetic-lead-secret"
KEY = "synthetic-ingest-key-0123456789"
TOKEN = "synthetic.token-value_abc123"
CASE_ID = "41"
ENVIRON = {smoke.PASSWORD_VARIABLE: PASSWORD, smoke.KEY_VARIABLE: KEY}
SECRETS = (PASSWORD, KEY, TOKEN)


class FakeApp:
    """Answers the probe's calls. `ingest` is a list of statuses (the last one repeats); the case is
    in the queue from the `queue_after`-th queue read after the ingest (None: never); `closing` maps
    "claim", "start" or "close" to the status that call answers."""

    def __init__(self, queue_after=1, ingest=(201,), retry_after="7", closing=None):
        self.queue_after, self.ingest, self.retry_after, self.closing = queue_after, list(ingest), retry_after, closing or {}
        self.calls = []  # (method, path, traceparent, body)
        self.ingests = 0
        self.reads_since_ingest = 0
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                app.calls.append((self.command, self.path, self.headers.get("traceparent"), body))
                status, payload, headers = app.answer(self.command, self.path, self.headers, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = handle_any

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def answer(self, method, path, headers, body):
        if path == "/api/login":
            ok = body == {"username": "lead", "password": PASSWORD}
            return (200, {"token": TOKEN}, {}) if ok else (401, {"code": "invalid_credentials"}, {})
        if path == "/api/alerts":
            status = self.ingest[min(self.ingests, len(self.ingest) - 1)]
            self.ingests += 1
            if headers.get("X-API-Key") != KEY:
                return 401, {"code": "invalid_api_key"}, {}
            if status == 429:
                return 429, {"code": "rate_limited"}, ({"Retry-After": self.retry_after} if self.retry_after else {})
            self.reads_since_ingest = 0
            return status, {"id": CASE_ID}, {}
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return 401, {"code": "unauthorized"}, {}
        if path == "/api/queue":
            if self.ingests:
                self.reads_since_ingest += 1
            found = self.queue_after is not None and self.reads_since_ingest >= self.queue_after
            return 200, {"cases": [{"id": "7"}] + ([{"id": CASE_ID}] if found else []), "servedAt": "x"}, {}
        step = "claim" if path.endswith("/claim") else ("close" if body and body["to"] == "Closed" else "start")
        status = self.closing.get(step, 200)
        return status, ({"id": CASE_ID} if status == 200 else {"code": "already_claimed"}), {}

    def paths(self):
        return [(method, path) for method, path, _, _ in self.calls]


class Clock:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def app():
    apps = []

    def make(**options):
        apps.append(FakeApp(**options))
        return apps[-1]

    yield make
    for each in apps:
        each.server.shutdown()
        each.server.server_close()


def run_probe(url, *args, environ=ENVIRON):
    clock, records = Clock(), []
    code = probe.main([url, *args], environ, lambda delay, trace_id: records.append((delay, trace_id)), clock.sleep, clock.monotonic)
    return code, records, clock


def trace_of(call):
    version, trace_id, parent, flags = call[2].split("-")
    assert (version, len(trace_id), len(parent), flags) == ("00", 32, 16, "01")  # sampled
    return trace_id


def test_watch_read_comes_before_the_ingest(app):
    fake = app()
    code, records, _ = run_probe(fake.url)
    assert code == 0
    assert fake.paths()[:3] == [("POST", "/api/login"), ("GET", "/api/queue"), ("POST", "/api/alerts")]
    assert fake.calls[1][2] is None  # the watch read carries no trace


def test_case_on_the_third_poll_records_two_to_three_seconds_with_the_trace_id(app, capsys):
    fake = app(queue_after=3)
    code, records, _ = run_probe(fake.url)
    ingest = next(call for call in fake.calls if call[1] == "/api/alerts")
    polls = [call for call in fake.calls[3:] if call[1] == "/api/queue"]
    trace_id = trace_of(ingest)
    assert code == 0
    assert len(polls) == 3 and {trace_of(call) for call in polls} == {trace_id}
    assert len(records) == 1 and 2 <= records[0][0] <= 3 and records[0][1] == trace_id
    assert capsys.readouterr().out.splitlines()[0] == f"trace={trace_id} case={CASE_ID} delay={records[0][0]:.3f}"


def test_a_case_seen_after_more_than_five_seconds_exits_1(app):
    code, records, _ = run_probe(app(queue_after=7).url)
    assert code == 1 and records[0][0] == 6.0


def test_a_case_that_never_appears_records_30_and_exits_1(app, capsys):
    fake = app(queue_after=None)
    code, records, clock = run_probe(fake.url)
    assert code == 1
    assert [delay for delay, _ in records] == [30.0]
    assert clock.sleeps == [1.0] * 30
    assert "not seen" in capsys.readouterr().out
    assert [path for _, path in fake.paths()][-3:] == [f"/api/cases/{CASE_ID}/claim"] + [f"/api/cases/{CASE_ID}/transition"] * 2  # still closed


def test_a_429_waits_for_retry_after_and_records_nothing(app, capsys):
    fake = app(ingest=(429,))
    code, records, clock = run_probe(fake.url, "--count", "2")
    assert code == 0 and records == []
    assert clock.sleeps == [7]
    assert capsys.readouterr().out.count("rate limited") == 2
    assert all("/cases/" not in path for _, path in fake.paths())


def test_a_429_without_a_usable_retry_after_waits_the_ingest_window(app):
    _, _, clock = run_probe(app(ingest=(429,), retry_after="soon").url, "--count", "2")
    assert clock.sleeps == [60]


def test_the_close_sequence_is_claim_start_close_with_false_positive(app):
    fake = app()
    run_probe(fake.url)
    closing = [(method, path, body) for method, path, _, body in fake.calls[-3:]]
    assert closing == [
        ("POST", f"/api/cases/{CASE_ID}/claim", None),
        ("POST", f"/api/cases/{CASE_ID}/transition", {"to": "Investigating"}),
        ("POST", f"/api/cases/{CASE_ID}/transition", {"to": "Closed", "closingReason": "false_positive"}),
    ]


def test_a_failed_close_is_printed_as_a_status_and_the_next_cycle_runs(app, capsys):
    fake = app(closing={"claim": 409})
    code, records, _ = run_probe(fake.url, "--count", "2")
    out = capsys.readouterr().out
    assert code == 0 and len(records) == 2 and fake.ingests == 2
    assert out.count("close failed (claim: HTTP 409 code already_claimed)") == 2
    assert not any(path.endswith("/transition") for _, path in fake.paths())  # stops at the failed call


def test_count_and_interval_default_to_1_and_5_and_space_the_cycles(app):
    one = app()
    assert run_probe(one.url)[2].sleeps == [] and one.ingests == 1
    two = app()
    _, _, clock = run_probe(two.url, "--count", "2")
    assert two.ingests == 2 and clock.sleeps == [5.0]
    three = app()
    _, _, clock = run_probe(three.url, "--count", "3", "--interval", "2.5")
    assert clock.sleeps == [2.5, 2.5]


@pytest.mark.parametrize("args", [["--count", "0"], ["--count", "x"], ["--interval", "-1"], ["--interval", "nan"]])
def test_a_bad_count_or_interval_exits_2_before_any_request(app, args):
    fake = app()
    with pytest.raises(SystemExit) as stop:
        run_probe(fake.url, *args)
    assert stop.value.code == 2 and fake.calls == []


@pytest.mark.parametrize(
    "url",
    ["https://127.0.0.1:8000", "http://example.com", "http://app.example.com", "http://127.0.0.1:8000/api", "http://user@127.0.0.1:8000",
     "http://127.0.0.1:8000?x=1", "http://other:8000", "https://app", "http://app:8000/x", "http://app@evil.example", "", "http://[::1"],
)
def test_a_refused_url_exits_2_before_any_request(url, capsys, monkeypatch):
    monkeypatch.setattr(smoke.Client, "request", lambda *a, **k: pytest.fail("a request was sent"))
    code, records, _ = run_probe(url)
    err = capsys.readouterr().err
    assert code == 2 and records == []
    assert "refused" in err and url not in err or url == ""


@pytest.mark.parametrize("url,host,port", [("http://app:8000", "app", 8000), ("http://proxy", "proxy", None), ("http://localhost:8000/", "localhost", 8000), ("http://[::1]:8000", "::1", 8000)])
def test_the_stack_service_names_and_the_loopback_are_allowed(url, host, port):
    assert probe.parse_url(url) == ("http", host, port, "")


@pytest.mark.parametrize("missing", [smoke.PASSWORD_VARIABLE, smoke.KEY_VARIABLE])
def test_a_missing_variable_exits_2_naming_it_before_any_request(app, missing, capsys):
    fake = app()
    code, _, _ = run_probe(fake.url, environ={name: value for name, value in ENVIRON.items() if name != missing})
    err = capsys.readouterr().err
    assert code == 2 and fake.calls == [] and missing in err
    assert not any(secret in err for secret in SECRETS)


def test_a_wrong_password_is_reported_by_status_only_and_stops_the_run(app, capsys):
    fake = app()
    code, records, _ = run_probe(fake.url, "--count", "3", environ={**ENVIRON, smoke.PASSWORD_VARIABLE: "another-synthetic-password"})
    out = capsys.readouterr()
    assert code == 1 and records == [] and fake.paths() == [("POST", "/api/login")]
    assert "HTTP 401" in out.out and "another-synthetic-password" not in out.out + out.err


def test_a_wrong_key_is_reported_by_status_only(app, capsys):
    fake = app()
    code, records, _ = run_probe(fake.url, environ={**ENVIRON, smoke.KEY_VARIABLE: "another-synthetic-key-0123456789"})
    out = capsys.readouterr()
    assert code == 1 and records == [] and "HTTP 401" in out.out
    assert "another-synthetic-key" not in out.out + out.err


def test_the_alert_is_the_fixed_synthetic_text(app):
    fake = app()
    run_probe(fake.url)
    sent = next(call[3] for call in fake.calls if call[1] == "/api/alerts")
    assert {key: value for key, value in sent.items() if key != "timestamp"} == probe.SYNTHETIC_ALERT
    assert "SYNTHETIC" in sent["title"] and "Synthetic" in sent["description"]


def test_no_secret_is_printed(app, capsys):
    for options in ({}, {"queue_after": None}, {"ingest": (429,)}, {"closing": {"close": 409}}):
        fake = app(**options)
        run_probe(fake.url, "--count", "2")
    out = capsys.readouterr()
    assert out.out and not any(secret in out.out + out.err for secret in SECRETS)


# --- the OTLP histogram (#294, with #288 and #338): the SDK's in-memory reader, no network ---

CONTRACT_BOUNDS = (0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 30)  # observability/CONTRACT.md, section 2


def measured(reader):
    """(resource, the one histogram data point) of the probe's metric, from an in-memory reader."""
    data = reader.get_metrics_data()
    resource_metrics = data.resource_metrics
    assert len(resource_metrics) == 1
    metrics = [metric for scope in resource_metrics[0].scope_metrics for metric in scope.metrics]
    assert [metric.name for metric in metrics] == ["socboard.probe.time_to_queue"]
    assert metrics[0].unit == "s"
    return resource_metrics[0].resource, metrics[0].data.data_points


@pytest.fixture
def sdk():
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    return InMemoryMetricReader()


def run_with_histogram(url, reader, *args, environ=ENVIRON):
    clock = Clock()
    record, shutdown = probe.build_recorder(environ, reader=reader)
    code = probe.main([url, *args], environ, record, clock.sleep, clock.monotonic)
    return code, shutdown


def test_the_delay_is_in_the_histogram_with_the_cycle_trace_id_as_exemplar(app, sdk):
    fake = app(queue_after=3)
    code, _ = run_with_histogram(fake.url, sdk)
    trace_id = trace_of(next(call for call in fake.calls if call[1] == "/api/alerts"))
    _, points = measured(sdk)
    assert code == 0 and len(points) == 1
    point = points[0]
    assert point.count == 1 and 2 <= point.sum <= 3
    assert tuple(point.explicit_bounds) == CONTRACT_BOUNDS
    assert sum(point.bucket_counts) == 1
    assert [format(exemplar.trace_id, "032x") for exemplar in point.exemplars] == [trace_id]
    assert point.exemplars[0].value == point.sum
    assert dict(point.attributes) == {}  # no label: the exemplar is the only link to a trace


def test_the_resource_is_the_probe_whatever_the_environment_says(app, sdk, monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "socboard")
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "service.name=socboard,deployment.environment.name=ci,service.version=abc123")
    run_with_histogram(app().url, sdk)
    resource, _ = measured(sdk)
    assert resource.attributes["service.name"] == "socboard-probe"
    assert resource.attributes["deployment.environment.name"] == "ci"  # operator-set, as the contract says
    assert resource.attributes["service.version"] == "abc123"


def test_a_case_that_never_appears_is_recorded_at_the_top_bucket_bound(app, sdk):
    code, _ = run_with_histogram(app(queue_after=None).url, sdk)
    _, points = measured(sdk)
    assert code == 1
    assert points[0].count == 1 and points[0].sum == 30.0
    assert points[0].bucket_counts[-2] == 1  # the (15, 30] bucket; the overflow bucket stays empty
    assert points[0].bucket_counts[-1] == 0


def test_a_429_records_nothing_in_the_histogram(app, sdk):
    run_with_histogram(app(ingest=(429,)).url, sdk)
    assert sdk.get_metrics_data() is None or all(
        not scope.metrics for each in sdk.get_metrics_data().resource_metrics for scope in each.scope_metrics
    )


def test_each_cycle_records_one_point_with_its_own_exemplar(app, sdk):
    fake = app()
    run_with_histogram(fake.url, sdk, "--count", "2")
    ingests = [trace_of(call) for call in fake.calls if call[1] == "/api/alerts"]
    _, points = measured(sdk)
    assert points[0].count == 2 and len(set(ingests)) == 2
    assert {format(exemplar.trace_id, "032x") for exemplar in points[0].exemplars} <= set(ingests)


def test_with_no_endpoint_nothing_is_created_and_the_packages_are_not_needed():
    for environ in ({}, {"OTEL_EXPORTER_OTLP_ENDPOINT": ""}, {"OTEL_EXPORTER_OTLP_ENDPOINT": "   "}):
        record, shutdown = probe.build_recorder(environ)
        assert record is None and shutdown() is None


def test_the_recorder_is_flushed_when_the_run_ends_even_after_a_failure(app, monkeypatch):
    flushed = []
    monkeypatch.setattr(probe, "build_recorder", lambda environ: (lambda delay, trace_id: None, lambda: flushed.append(True)))
    clock = Clock()
    code = probe.main([app(queue_after=None).url], ENVIRON, None, clock.sleep, clock.monotonic)
    assert code == 1 and flushed == [True]


def test_no_secret_reaches_the_histogram(app, sdk):
    run_with_histogram(app().url, sdk)
    text = repr(sdk.get_metrics_data())
    assert not any(secret in text for secret in SECRETS)


def test_the_histogram_is_exported_over_otlp_http_when_the_run_ends(app, monkeypatch):
    """A fake collector on the loopback receives the OTLP request: the probe's resource, the metric, the exemplar."""
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest

    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.rfile.read(int(self.headers.get("Content-Length") or 0))))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    collector.daemon_threads = True
    threading.Thread(target=lambda: collector.serve_forever(poll_interval=0.02), daemon=True).start()
    endpoint = f"http://127.0.0.1:{collector.server_address[1]}"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)  # the exporter reads the process environment
    try:
        fake = app()
        clock = Clock()
        code = probe.main([fake.url], {**ENVIRON, "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint}, None, clock.sleep, clock.monotonic)
        trace_id = trace_of(next(call for call in fake.calls if call[1] == "/api/alerts"))
    finally:
        collector.shutdown()
        collector.server_close()
    assert code == 0
    assert [path for path, _ in received] == ["/v1/metrics"]
    request = ExportMetricsServiceRequest()
    request.ParseFromString(received[0][1])
    resource_metrics = request.resource_metrics[0]
    assert {a.key: a.value.string_value for a in resource_metrics.resource.attributes}["service.name"] == "socboard-probe"
    metric = resource_metrics.scope_metrics[0].metrics[0]
    assert (metric.name, metric.unit) == ("socboard.probe.time_to_queue", "s")
    point = metric.histogram.data_points[0]
    assert point.count == 1 and tuple(point.explicit_bounds) == CONTRACT_BOUNDS
    assert [exemplar.trace_id.hex() for exemplar in point.exemplars] == [trace_id]
