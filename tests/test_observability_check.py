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
COLLECTOR_PREFIX = "socboard-otel-collector-1  | "  # what `docker compose logs` puts before each line
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
            if self.flags.get("hostile_store_strings"):  # a store answer whose strings are not the known ones
                spans.append(self.span(CANARIES["case-note"], "55" * 8, SERVER_ID))
                spans[0]["startTimeUnixNano"] = CANARIES["token"]
        elif trace_id == self.canary_trace_id:
            spans = [self.span("POST /api/alerts", SERVER_ID), self.span("ingest.alert", INGEST_ID, SERVER_ID)]
            if self.flags.get("canary_in_span_attribute"):
                spans[1]["attributes"] = [{"key": "note", "value": {"stringValue": CANARIES[self.flags["canary_in_span_attribute"]]}}]
            if self.flags.get("canary_in_span_name"):
                spans.append(self.span(CANARIES[self.flags["canary_in_span_name"]], "66" * 8, SERVER_ID))
        elif trace_id == QUEUE_TRACE and not self.flags.get("no_queue_span"):
            link_trace = "9" * 32 if self.flags.get("link_to_another_trace") else self.trace_id  # the span id is the ingest span's in both
            link = (link_trace, INGEST_ID if not self.flags.get("wrong_link") else "44" * 8)
            spans = [self.span("queue.first_seen", QUEUE_ID, links=[] if self.flags.get("no_link") else [link])]
        elif self.flags.get("tempo_truncated") and trace_id.startswith("0" * 24):
            spans = [self.span("POST /api/alerts", SERVER_ID)]  # a readable, clean trace: only the cut is wrong
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
            metadata = {"trace_id": trace_id, "event": event}
            if self.flags.get("canary_in_metadata"):
                metadata["note"] = CANARIES[self.flags["canary_in_metadata"]]
            time_ns = CANARIES["ingest-key"] if self.flags.get("hostile_store_strings") and trace_id == self.trace_id else str(1_000_000_000)
            lines.append([time_ns, line, {"structuredMetadata": metadata}])
        return lines

    def every_line(self, params):
        """The answer to the every-line query: only the selectors the script may use match; limit and direction are honoured."""
        if params["query"][0] not in ('{service_name=~".+"}', '{service_name="socboard"}') or self.flags.get("empty_loki"):
            return []
        skipped = {"probe": self.trace_id, "canary": self.canary_trace_id}.get(self.flags.get("all_lines_skip"))
        values = [[str(i), f"older filler line {i}", {}] for i in range(self.flags.get("filler", 0))]
        values += [value for each in (self.trace_id, self.canary_trace_id) if each != skipped for value in self.log_lines(each)]
        limit = int(params["limit"][0])
        values = values[-limit:] if params["direction"][0] == "backward" else values[:limit]  # a forward cut drops the newest lines
        labels = {"service_name": "socboard"}
        if self.flags.get("canary_in_loki_label"):
            labels["note"] = CANARIES[self.flags["canary_in_loki_label"]]
        return [{"stream": labels, "values": values}] if values else []

    def exemplars(self, metric):
        other = "c" * 32
        if metric == check.ALL_SERIES:  # every series, as the canary search asks
            found = [each for name in (check.ALERT_HISTOGRAM, check.PROBE_HISTOGRAM) for each in self.exemplars(name)]
            labels = {"trace_id": self.trace_id}
            if self.flags.get("canary_in_exemplar"):
                labels["note"] = CANARIES[self.flags["canary_in_exemplar"]]
            return found + [{"seriesLabels": {"__name__": "socboard_other_seconds_bucket"}, "exemplars": [{"labels": labels, "value": 1, "timestamp": 1.0}]}]
        wrong = self.flags.get("other_exemplar") == metric
        if self.flags.get("no_exemplar") == metric:
            return []
        timestamp = CANARIES["password"] if self.flags.get("hostile_store_strings") else 1.0
        return [{"seriesLabels": {"__name__": metric}, "exemplars": [{"labels": {"trace_id": other if wrong else self.trace_id}, "value": 1, "timestamp": timestamp}]}]

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
            if "queue.first_seen" not in params["q"][0]:
                if self.flags.get("unreadable_hit"):
                    ids.append("e" * 32)  # listed by the search, 404 when read
                if self.flags.get("tempo_truncated"):
                    ids += [f"{i:032x}" for i in range(1, int(params["limit"][0]) + 1)]
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
                result = self.every_line(params)
            return 200, json.dumps({"data": {"result": result}}).encode()
        if path == "/loki/api/v1/labels":
            names = ["service_name"] + ([CANARIES[self.flags["canary_in_loki_label_name"]]] if self.flags.get("canary_in_loki_label_name") else [])
            return 200, json.dumps({"data": names}).encode()
        if path == "/api/v1/query_exemplars":
            return 200, json.dumps({"data": self.exemplars(params["query"][0])}).encode()
        if path == "/api/v1/series":
            series = {"__name__": check.ALERT_HISTOGRAM}
            if self.flags.get("canary_in_series"):
                series["note"] = CANARIES[self.flags["canary_in_series"]]
            if self.flags.get("no_series"):
                return 200, json.dumps({"data": []}).encode()
            return 200, json.dumps({"data": [{"__name__": "x"} if self.flags.get("no_socboard_series") else series]}).encode()
        if path == "/api/v1/labels":
            names = ["__name__"] + ([CANARIES[self.flags["canary_in_prometheus_label_name"]]] if self.flags.get("canary_in_prometheus_label_name") else [])
            return 200, json.dumps({"data": names}).encode()
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


@pytest.mark.parametrize("flag", ["no_link", "wrong_link", "no_queue_span", "link_to_another_trace"])
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


@pytest.mark.parametrize(
    ("store", "flag"),
    [
        ("tempo", "canary_in_span_attribute"),
        ("tempo", "canary_in_span_name"),
        ("loki", "canary_in_log"),
        ("loki", "canary_in_metadata"),
        ("loki", "canary_in_loki_label"),
        ("loki", "canary_in_loki_label_name"),
        ("prometheus", "canary_in_series"),
        ("prometheus", "canary_in_prometheus_label_name"),
        ("prometheus", "canary_in_exemplar"),  # an exemplar of a series that is neither of the two histograms
    ],
)
def test_a_canary_in_each_place_of_each_store_fails_naming_that_store_and_the_kind(store, flag):
    code, report, output, _ = run_check(Stores(**{flag: "case-note"}))
    assert code == 1
    assert f"FAIL canary {store}: found canary kinds: case-note" in output
    assert output.count("FAIL canary") == 1  # no other store is blamed
    assert CANARIES["case-note"] not in output + json.dumps(report)


@pytest.mark.parametrize(
    ("store", "flags"),
    [
        ("loki", {"empty_loki": True}),
        ("loki", {"all_lines_skip": "probe"}),
        ("loki", {"all_lines_skip": "canary"}),
        ("prometheus", {"no_series": True}),
        ("prometheus", {"no_socboard_series": True}),
        ("tempo", {"tempo_truncated": True}),
        ("tempo", {"unreadable_hit": True}),
    ],
)
def test_an_empty_or_partial_answer_is_cannot_tell_and_fails_not_none_found(store, flags):
    code, report, output, _ = run_check(Stores(**flags))
    assert code == 1
    assert f"FAIL canary {store}: " in output and "none of the" not in output.split(f"canary {store}")[1].split("\n")[0]
    assert output.count("FAIL canary") == 1 and "FAIL tempo" not in output and "FAIL loki" not in output
    assert report["canary"]["stores"][store] == {"found": "cannot tell"}


@pytest.mark.parametrize(("filler", "fails"), [(2, False), (3, True), (10, True)])
def test_a_loki_answer_that_reaches_the_line_limit_is_cannot_tell(monkeypatch, filler, fails):
    monkeypatch.setattr(check, "LOG_LIMIT", 6)  # the run's own lines are 3; filler lines older than them fill up the answer
    stores = Stores(filler=filler)
    code, _, output, _ = run_check(stores)
    if fails:
        assert code == 1 and "FAIL canary loki: cannot tell: the answer reached its limit of 6 lines" in output
    else:
        assert code == 0, output
    every_line = [url for url in stores.requests if "query_range" in urllib.parse.unquote(url) and "service_name%3D~" in url]
    assert every_line and all("direction=backward" in url for url in every_line)  # the newest lines are the ones kept


def test_the_report_keeps_only_known_span_names_digit_times_and_no_store_string():
    code, report, output, _ = run_check(Stores(hostile_store_strings=True))
    assert code == 1 and "FAIL canary tempo" in output  # the hostile span name is a canary in Tempo
    text = json.dumps(report)
    for kind in ("case-note", "token", "password", "ingest-key"):
        assert CANARIES[kind] not in text
    spans = report["tempo"]["spans"]
    assert {span["name"] for span in spans} == {"POST /api/alerts", "ingest.alert"}
    assert all(span["start"] == "" or span["start"].isdigit() for span in spans)
    assert all(line["time"] == "" or line["time"].isdigit() for line in report["loki"]["lines"])
    assert all(each["time"] is None or isinstance(each["time"], float) for each in report["prometheus"]["exemplars"].values())


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
    log.write_text(f"{COLLECTOR_PREFIX}info something\n{COLLECTOR_PREFIX}attribute note={CANARIES['case-note']}\n")
    lines = []
    assert check.main(["collector-log", str(log), "--report", str(tmp_path / "r.json")], {}, say=lines.append) == 1
    assert "case-note" in lines[0] and CANARIES["case-note"] not in lines[0]


def test_a_clean_collector_log_passes_and_updates_the_report(tmp_path):
    log, report = tmp_path / "collector.log", tmp_path / "r.json"
    log.write_text(f"{COLLECTOR_PREFIX}info starting\n")
    report.write_text(json.dumps({"canary": {"stores": {}}}))
    assert check.main(["collector-log", str(log), "--report", str(report)], {}, say=lambda line: None) == 0
    assert json.loads(report.read_text())["canary"]["stores"]["collector"] == {"found": []}


def test_a_collector_log_without_a_collector_line_fails_even_when_clean(tmp_path):
    log, lines = tmp_path / "collector.log", []
    log.write_text("no such service: otel-collector\n")  # what a failed `compose logs` leaves in the file
    assert check.main(["collector-log", str(log), "--report", str(tmp_path / "r.json")], {}, say=lines.append) == 1
    assert "FAIL canary collector log" in lines[0] and "proves nothing" in lines[0]


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


# The ingest answer without a case id

def test_an_ingest_answer_without_a_case_id_is_a_problem_and_no_note_is_sent():
    app = FakeApp()
    base = app.answer
    app.answer = lambda method, path, headers, body: (201, {}) if path == "/api/alerts" and headers.get("X-API-Key") == KEY else base(method, path, headers, body)
    try:
        problems = check.canary_traffic(app.url, PASSWORD, KEY, CANARIES, "d" * 32)
    finally:
        app.server.shutdown()
        app.server.server_close()
    assert any(problem.startswith("note:") and "not sent" in problem for problem in problems)
    assert not any(path.endswith("/notes") for _, path, _, _ in app.seen)


# The probe's OTLP endpoint

def test_the_probe_exports_to_the_loopback_store_whatever_the_environment_says_and_the_environment_is_restored(monkeypatch):
    ambient = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com:4318",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "https://example.com:4318/v1/metrics",
        "OTEL_EXPORTER_OTLP_HEADERS": "authorization=Bearer ambient-secret",
        "OTEL_SERVICE_NAME": "kept",
    }
    for name, value in ambient.items():
        monkeypatch.setenv(name, value)
    seen = {}

    def fake_main(argv, environ):
        seen["process"] = dict(check.os.environ)
        seen["argv"], seen["environ"] = argv, environ
        return 0

    monkeypatch.setattr(check.probe, "main", fake_main)
    assert check.run_probe("http://127.0.0.1:8000", PASSWORD, KEY, "http://127.0.0.1:4318", "d" * 32) == 0
    otlp = {name: value for name, value in seen["process"].items() if name.startswith("OTEL_EXPORTER_OTLP_")}
    assert otlp == {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
    assert seen["environ"][check.probe.ENDPOINT_VARIABLE] == "http://127.0.0.1:4318"
    assert seen["process"]["OTEL_SERVICE_NAME"] == "kept"
    assert {name: check.os.environ.get(name) for name in ambient} == ambient


# The printed report and the step summary

def run_main(tmp_path, stores, environ=None):
    clock, lines = Clock(), []

    def probe_with_id(trace_id):
        stores.trace_id = trace_id
        return 0

    def canaries_with_id(trace_id):
        stores.canary_trace_id = trace_id
        return []

    report = tmp_path / "out" / "check.json"
    code = check.main(
        ["run", "--report", str(report), "--secrets-file", str(tmp_path / "none")], {**SECRETS, **(environ or {})},
        say=lines.append, drive_probe=probe_with_id, drive_canaries=canaries_with_id,
        get=stores.get, sleep=clock.sleep, clock=clock.monotonic, wall=clock.wall,
    )
    return code, report, lines


def test_show_prints_the_redacted_report_between_marker_lines_with_no_canary(tmp_path):
    code, report, _ = run_main(tmp_path, Stores(hostile_store_strings=True))
    assert code == 1  # the hostile span name is a canary in Tempo; the report must still hold none
    printed = []
    assert check.main(["show", "--report", str(report)], {}, say=printed.append) == 0
    assert printed[0] == check.BEGIN_MARK and printed[-1] == check.END_MARK
    body = "\n".join(printed[1:-1])
    assert json.loads(body)["traceId"] and "results" in json.loads(body)
    text = "\n".join(printed)
    assert not any(value in text for value in CANARIES.values()) and PASSWORD not in text and KEY not in text


def test_show_refuses_to_print_a_report_that_holds_a_canary_and_names_only_the_kind(tmp_path):
    report = tmp_path / "check.json"
    report.write_text(json.dumps({"traceId": "a" * 32, "note": CANARIES["token"]}))
    printed = []
    assert check.main(["show", "--report", str(report)], {}, say=printed.append) == 1
    assert printed == ["FAIL report holds canary kinds: token; it is not printed"]


def test_show_without_a_report_prints_a_note_and_exits_0(tmp_path):
    printed = []
    assert check.main(["show", "--report", str(tmp_path / "none.json")], {}, say=printed.append) == 0
    assert len(printed) == 1 and "nothing to print" in printed[0]


def test_show_appends_a_result_table_to_the_step_summary_only_when_it_is_set(tmp_path):
    _, report, _ = run_main(tmp_path, Stores())
    summary = tmp_path / "summary.md"
    summary.write_text("earlier\n")
    assert check.main(["show", "--report", str(report)], {"GITHUB_STEP_SUMMARY": str(summary)}, say=lambda line: None) == 0
    text = summary.read_text()
    assert text.startswith("earlier\n") and "| Result | Line |" in text
    assert "| ok | tempo: found" in text and "| FAIL" not in text
    assert not any(value in text for value in CANARIES.values())
    before = sorted(path.name for path in tmp_path.rglob("*"))
    assert check.main(["show", "--report", str(report)], {}, say=lambda line: None) == 0  # no variable: no file is written
    assert sorted(path.name for path in tmp_path.rglob("*")) == before


def test_a_failed_result_is_a_fail_row_in_the_summary(tmp_path):
    _, report, _ = run_main(tmp_path, Stores(no_log=True))
    summary = tmp_path / "summary.md"
    check.main(["show", "--report", str(report)], {"GITHUB_STEP_SUMMARY": str(summary)}, say=lambda line: None)
    assert "| FAIL | FAIL loki" in summary.read_text()
