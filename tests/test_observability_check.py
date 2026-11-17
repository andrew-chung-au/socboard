"""Tests for scripts/observability_check.py (#295), against fake stores: no Docker, no network.

The stores are a function `get(url, headers) -> (status, body)` injected where the script would fetch,
the probe and the canary traffic are fakes that tell the stores the trace ids, and the clock is a fake,
so a 60-second deadline takes no time. The canary traffic itself runs once against a small local HTTP
server. Every value here is synthetic; the canaries are read from observability/CONTRACT.md.
"""

import base64
import importlib.util
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from socboard import smoke
from observability_support import canaries as contract_canaries

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("observability_check", ROOT / "scripts" / "observability_check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

CANARIES = contract_canaries()
QUEUE_TRACE = "f" * 32
SERVER_ID, INGEST_ID, QUEUE_ID = "11" * 8, "22" * 8, "33" * 8
URLS = {"tempo": "http://t", "loki": "http://l", "prometheus": "http://p", "otlp": "http://o"}
PASSWORD, KEY = "Zq7-synthetic-lead-secret", "synthetic-ingest-key-0123456789"
SECRETS = {smoke.PASSWORD_VARIABLE: PASSWORD, smoke.KEY_VARIABLE: KEY}


def b64(hex_text):
    return base64.b64encode(bytes.fromhex(hex_text)).decode()


class Stores:
    """What Tempo, Loki and Prometheus answer. A flag breaks one signal."""

    def __init__(self, **flags):
        self.flags = flags
        self.trace_id = self.canary_trace_id = None
        self.requests = []

    def span(self, name, span_id, parent="", links=()):
        ids = b64 if self.flags.get("base64_ids") else (lambda text: text)
        return {"name": name, "spanId": ids(span_id), "parentSpanId": ids(parent) if parent else "", "links": [{"traceId": ids(t), "spanId": ids(s)} for t, s in links]}

    def trace(self, trace_id):
        if trace_id == self.trace_id and not self.flags.get("no_trace"):
            spans = [self.span("POST /api/alerts", SERVER_ID), self.span("ingest.alert", INGEST_ID, SERVER_ID)]
        elif trace_id == self.canary_trace_id:
            spans = [self.span("POST /api/alerts", SERVER_ID), self.span("ingest.alert", INGEST_ID, SERVER_ID)]
        elif trace_id == QUEUE_TRACE and not self.flags.get("no_queue_span"):
            link = (self.trace_id, INGEST_ID if not self.flags.get("wrong_link") else "44" * 8)
            spans = [self.span("queue.first_seen", QUEUE_ID, links=[] if self.flags.get("no_link") else [link])]
        else:
            return None
        return {"batches": [{"scopeSpans": [{"spans": spans}]}]}

    def log_lines(self, trace_id):
        events = ["alert.ingested", "alert.first_in_queue"] if trace_id == self.trace_id else ["alert.ingested"]
        if self.flags.get("no_log") and trace_id == self.trace_id:
            return []
        lines = []
        for event in events:
            line = json.dumps({"event": event, "trace_id": trace_id})
            if self.flags.get("canary_in_log"):
                line = json.dumps({"event": event, "trace_id": trace_id, "note": CANARIES[self.flags["canary_in_log"]]})
            lines.append([str(1_000_000_000), line, {"structuredMetadata": {"trace_id": trace_id, "event": event}}])
        return lines

    def exemplars(self, metric):
        other = "c" * 32
        wrong = self.flags.get("other_exemplar") == metric
        if self.flags.get("no_exemplar") == metric:
            return []
        return [{"seriesLabels": {"__name__": metric}, "exemplars": [{"labels": {"trace_id": other if wrong else self.trace_id}, "value": 1, "timestamp": 1.0}]}]

    def get(self, url, headers):
        self.requests.append(url)
        if self.flags.get("silent"):
            return 0, b""
        parts = urllib.parse.urlsplit(url)
        params = urllib.parse.parse_qs(parts.query)
        path = parts.path
        if path.startswith("/api/traces/"):
            document = self.trace(path.rsplit("/", 1)[1])
            return (200, json.dumps(document).encode()) if document else (404, b"not found")
        if path == "/api/search":
            ids = [QUEUE_TRACE] if "queue.first_seen" in params["q"][0] else [self.trace_id, self.canary_trace_id, QUEUE_TRACE]
            return 200, json.dumps({"traces": [{"traceID": each} for each in ids]}).encode()
        if path == "/loki/api/v1/query_range":
            selector = params["query"][0]
            if "trace_id=" in selector:
                wanted = selector.split('trace_id="')[1].rstrip('"')
                values, labels = self.log_lines(wanted), {"service_name": "socboard"}
                if self.flags.get("trace_id_label") and wanted == self.trace_id:
                    labels["trace_id"] = wanted
                    values = [[ts, line, {}] for ts, line, _ in values]
                result = [{"stream": labels, "values": values}] if values else []
            else:
                result = [{"stream": {"service_name": "socboard"}, "values": self.log_lines(self.trace_id) + self.log_lines(self.canary_trace_id)}]
            return 200, json.dumps({"data": {"result": result}}).encode()
        if path == "/loki/api/v1/labels":
            return 200, json.dumps({"data": ["service_name"]}).encode()
        if path == "/api/v1/query_exemplars":
            return 200, json.dumps({"data": self.exemplars(params["query"][0])}).encode()
        if path in ("/api/v1/series", "/api/v1/labels"):
            leak = self.flags.get("canary_in_series")
            return 200, json.dumps({"data": [{"__name__": "x", "note": CANARIES[leak]}] if leak else []}).encode()
        return 404, b""


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def monotonic(self):
        return self.now

    def wall(self):
        return 1_000_000.0 + self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def run_check(stores, deadline=60.0, traffic_problems=()):
    clock, lines = Clock(), []

    def drive_probe(trace_id):
        stores.trace_id = trace_id
        return 0

    def drive_canaries(trace_id):
        stores.canary_trace_id = trace_id
        return list(traffic_problems)

    code, report = check.check(
        "a" * 32, "b" * 32, URLS, CANARIES, deadline, lines.append, drive_probe, drive_canaries,
        stores.get, clock.sleep, clock.monotonic, clock.wall,
    )
    return code, report, "\n".join(lines), clock


def test_the_full_pass():
    code, report, output, _ = run_check(Stores())
    assert code == 0, output
    assert "trace id: " + "a" * 32 in output
    for signal in ("tempo: found", "loki: found", "prometheus socboard_alert_time_to_queue_seconds_bucket: found", "prometheus socboard_probe_time_to_queue_seconds_bucket: found"):
        assert signal in output
    assert report["canary"]["stores"] == {s: {"found": []} for s in ("tempo", "loki", "prometheus")}
    assert report["tempo"]["queue_first_seen"]["traceId"] == QUEUE_TRACE


def test_tempo_ids_in_base64_are_read():
    code, _, output, _ = run_check(Stores(base64_ids=True))
    assert code == 0, output


def test_the_trace_without_a_log_line_fails_naming_loki():
    code, _, output, _ = run_check(Stores(no_log=True))
    assert code == 1
    assert "FAIL loki" in output and "no log line" in output
    assert "FAIL tempo" not in output


def test_trace_id_as_a_label_fails_naming_the_label():
    code, _, output, _ = run_check(Stores(trace_id_label=True))
    assert code == 1
    assert "FAIL loki" in output and "stream label, not structured metadata" in output


@pytest.mark.parametrize("metric", [check.ALERT_HISTOGRAM, check.PROBE_HISTOGRAM])
def test_an_exemplar_with_another_trace_id_fails_naming_the_histogram(metric):
    code, _, output, _ = run_check(Stores(other_exemplar=metric))
    assert code == 1
    assert f"FAIL prometheus {metric}" in output and "another trace id" in output
    assert output.count("FAIL prometheus") == 1


@pytest.mark.parametrize("flag", ["no_link", "wrong_link", "no_queue_span"])
def test_a_missing_queue_first_seen_link_fails_naming_tempo(flag):
    code, _, output, _ = run_check(Stores(**{flag: True}))
    assert code == 1
    assert "FAIL tempo" in output and "queue.first_seen" in output
    assert "FAIL loki" not in output


def test_a_missing_trace_fails_naming_tempo():
    code, _, output, _ = run_check(Stores(no_trace=True))
    assert code == 1 and "FAIL tempo" in output


@pytest.mark.parametrize("kind", sorted(CANARIES))
def test_a_canary_in_a_log_line_fails_naming_the_kind_and_never_the_value(kind):
    code, report, output, _ = run_check(Stores(canary_in_log=kind))
    assert code == 1
    assert "FAIL canary loki" in output and kind in output
    assert CANARIES[kind] not in output + json.dumps(report)


def test_a_canary_in_a_prometheus_series_fails_naming_prometheus():
    code, _, output, _ = run_check(Stores(canary_in_series="token"))
    assert code == 1 and "FAIL canary prometheus" in output and "token" in output
    assert CANARIES["token"] not in output


def test_a_store_that_never_answers_hits_each_deadline_without_sleeping_for_real():
    code, _, output, clock = run_check(Stores(silent=True), deadline=60.0)
    assert code == 1
    for signal in ("tempo", "loki", "prometheus " + check.ALERT_HISTOGRAM, "prometheus " + check.PROBE_HISTOGRAM):
        assert f"FAIL {signal}" in output
    assert "FAIL canary search" in output  # the canary run's own signals never arrived, so the search proves nothing
    assert 6 * 60 <= clock.now < 6 * 60 + 6 * 3  # six polled lookups, each up to its deadline
    assert max(clock.sleeps) <= 2.0


def test_a_failed_canary_request_is_reported_and_fails():
    code, _, output, _ = run_check(Stores(), traffic_problems=["failed sign-in: HTTP 200, expected HTTP 401"])
    assert code == 1 and "FAIL canary traffic: failed sign-in" in output


# The refused base URL

@pytest.mark.parametrize("url", ["http://example.com:8000", "https://127.0.0.1:8000", "http://app:8000", "http://127.0.0.1:8000/api", "http://u:p@127.0.0.1:8000", "http://127.0.0.1:8000?x=1"])
def test_a_base_url_that_is_not_loopback_http_is_refused_before_any_request(url, capsys):
    stores, calls = Stores(), []
    code = check.main(["run", "--url", url, "--secrets-file", "none"], SECRETS, drive_probe=lambda i: calls.append(i), drive_canaries=lambda i: calls.append(i), get=stores.get)
    assert code == 2
    assert calls == [] and stores.requests == []
    assert "refused" in capsys.readouterr().err


def test_main_runs_the_check_and_writes_a_redacted_report(tmp_path):
    stores, lines = Stores(), []

    def probe_with_id(trace_id):
        stores.trace_id = trace_id
        return 0

    def canaries_with_id(trace_id):
        stores.canary_trace_id = trace_id
        return []

    clock = Clock()
    report = tmp_path / "out" / "check.json"
    code = check.main(
        ["run", "--report", str(report), "--secrets-file", str(tmp_path / "none")], SECRETS,
        say=lines.append, drive_probe=probe_with_id, drive_canaries=canaries_with_id,
        get=stores.get, sleep=clock.sleep, clock=clock.monotonic, wall=clock.wall,
    )
    assert code == 0, lines
    text = report.read_text()
    assert json.loads(text)["traceId"] == stores.trace_id
    for secret in (PASSWORD, KEY, *CANARIES.values()):
        assert secret not in text


def test_credentials_are_read_from_the_secrets_file(tmp_path):
    file = tmp_path / "compose.secrets"
    file.write_text(f"{smoke.KEY_VARIABLE}={KEY}\nOTHER=x\n{smoke.PASSWORD_VARIABLE}={PASSWORD}\n")
    assert check.read_secrets(file, {}) == SECRETS


def test_a_missing_credential_is_refused_naming_the_variable(tmp_path, capsys):
    assert check.main(["run", "--secrets-file", str(tmp_path / "none")], {}) == 2
    assert smoke.KEY_VARIABLE in capsys.readouterr().err


# The collector's log

def test_the_collector_log_is_searched_and_only_the_kind_is_printed(tmp_path):
    log = tmp_path / "collector.log"
    log.write_text(f"info something\nattribute note={CANARIES['case-note']}\n")
    lines = []
    assert check.main(["collector-log", str(log), "--report", str(tmp_path / "r.json")], {}, say=lines.append) == 1
    assert "case-note" in lines[0] and CANARIES["case-note"] not in lines[0]


def test_a_clean_collector_log_passes_and_updates_the_report(tmp_path):
    log, report = tmp_path / "collector.log", tmp_path / "r.json"
    log.write_text("info starting\n")
    report.write_text(json.dumps({"canary": {"stores": {}}}))
    assert check.main(["collector-log", str(log), "--report", str(report)], {}, say=lambda line: None) == 0
    assert json.loads(report.read_text())["canary"]["stores"]["collector"] == {"found": []}


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_a_missing_or_empty_collector_log_fails(tmp_path, content):
    log = tmp_path / "collector.log"
    if content is not None:
        log.write_text(content)
    assert check.main(["collector-log", str(log), "--report", str(tmp_path / "r.json")], {}, say=lambda line: None) == 1


# The canary traffic, against a small local app

class FakeApp:
    def __init__(self):
        self.seen = []  # (method, path, headers, body)
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                app.seen.append((self.command, self.path, dict(self.headers), body))
                status, payload = app.answer(self.command, self.path, self.headers, body)
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = handle_any

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def answer(self, method, path, headers, body):
        if path == "/api/login":
            return (200, {"token": "synthetic.token-value"}) if body["password"] == PASSWORD else (401, {"code": "invalid_credentials"})
        if path == "/api/alerts":
            return (201, {"id": "case-1"}) if headers.get("X-API-Key") == KEY else (401, {"code": "invalid_api_key"})
        if path == "/api/cases/case-1/notes":
            return (201, {"id": "n1"}) if headers.get("Authorization") == "Bearer synthetic.token-value" else (401, {})
        return 401, {"code": "unauthorized"}


def test_the_canary_traffic_sends_each_canary_where_the_issue_says_and_every_refusal_is_a_401():
    app = FakeApp()
    try:
        trace_id = "d" * 32
        assert check.canary_traffic(app.url, PASSWORD, KEY, CANARIES, trace_id) == []
    finally:
        app.server.shutdown()
        app.server.server_close()
    by_step = {(method, path): (headers, body) for method, path, headers, body in app.seen if body is None or body.get("title") != "SYNTHETIC refused alert"}
    ingest = next(body for method, path, _, body in app.seen if path == "/api/alerts" and body["title"] == CANARIES["alert-title"])
    assert ingest["description"] == CANARIES["alert-description"]
    assert by_step[("POST", "/api/cases/case-1/notes")][1] == {"text": CANARIES["case-note"]}
    assert any(path == "/api/login" and body["password"] == CANARIES["password"] for _, path, _, body in app.seen)
    assert any(headers.get("X-API-Key") == CANARIES["ingest-key"] for _, _, headers, _ in app.seen)
    assert any(headers.get("Authorization") == f"Bearer {CANARIES['token']}" for _, _, headers, _ in app.seen)
    assert all(headers["traceparent"].startswith(f"00-{trace_id}-") for _, _, headers, _ in app.seen[1:])  # all but the first sign-in (smoke.step_login sends none)


def test_a_canary_request_that_is_not_refused_is_a_problem():
    app = FakeApp()
    app.answer = lambda method, path, headers, body: (200, {"token": "synthetic.token-value", "id": "case-1"})
    try:
        problems = check.canary_traffic(app.url, PASSWORD, KEY, CANARIES, "d" * 32)
    finally:
        app.server.shutdown()
        app.server.server_close()
    assert any(problem.startswith("failed sign-in") for problem in problems)
    assert all(CANARIES[kind] not in " ".join(problems) for kind in CANARIES)


# The names

def test_the_names_are_the_contracts():
    contract = yaml.safe_load((ROOT / "observability" / "contract.yaml").read_text())
    prometheus = {name for metric in contract["metrics"] for name in metric["prometheus_names"]}
    assert {check.ALERT_HISTOGRAM, check.PROBE_HISTOGRAM} <= prometheus
    spans = {span["name"] for span in contract["spans"]}
    assert {check.SERVER_SPAN, check.INGEST_SPAN, check.QUEUE_SPAN} <= spans
    assert set(check.EVENTS) == {event["name"] for event in contract["log_events"]}


def test_the_canaries_are_the_contracts_seven():
    assert set(CANARIES) == {"alert-title", "alert-description", "case-note", "password", "ingest-key", "token", "account-number"}
    assert all(value.startswith("CANARY-") for value in CANARIES.values())
