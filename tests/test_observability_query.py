"""Tests for observability/ci/query.py (#340), against fake stores.

No Docker. The checks take the fetch function, the clock and the sleep as arguments, so a 60-second wait
takes no time; one test runs the real fetch against a stand-in HTTP server on the loopback to see the URLs
and headers actually sent. Every value is synthetic.
"""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "observability" / "ci" / "query.py"
spec = importlib.util.spec_from_file_location("observability_query", SCRIPT)
query = importlib.util.module_from_spec(spec)
spec.loader.exec_module(query)

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
OTHER_ID = "00f067aa0ba902b700f067aa0ba902b7"
URLS = {"tempo": "http://tempo", "loki": "http://loki", "prometheus": "http://prom", "alertmanager": "http://am", "grafana": "http://graf", "collector": "http://coll"}


def tempo_trace(names=("ingest.alert",)):
    spans = [{"name": name} for name in names]
    return 200, json.dumps({"batches": [{"scopeSpans": [{"spans": spans}]}]}).encode()


def loki_result(stream=None, metadata=None, lines=1):
    values = [["1700000000000000000", "alert.ingested", {"structuredMetadata": metadata or {}}] for _ in range(lines)]
    result = [{"stream": stream or {"service_name": "socboard"}, "values": values}] if lines else []
    return 200, json.dumps({"status": "success", "data": {"resultType": "streams", "result": result}}).encode()


def exemplars(*ids):
    series = [{"seriesLabels": {"__name__": query.EXEMPLAR_METRIC}, "exemplars": [{"labels": {"trace_id": i}, "value": 1.5, "timestamp": 1.7e9} for i in ids]}] if ids else []
    return 200, json.dumps({"status": "success", "data": series}).encode()


class Stores:
    """A fake `fetch`: each store answers with what it is given, and every request is kept."""

    def __init__(self, tempo=None, loki=None, prom=None, ready=200):
        self.tempo = tempo or tempo_trace()
        self.loki = loki or loki_result(metadata={"trace_id": TRACE_ID})
        self.prom = prom or exemplars(TRACE_ID)
        self.ready, self.requests = ready, []

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        if url.startswith("http://tempo/api/traces/"):
            return self.tempo
        if url.startswith("http://loki/loki/api/v1/query_range"):
            return self.loki
        if url.startswith("http://prom/api/v1/query_exemplars"):
            return self.prom
        return self.ready, b""


class Clock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def run_find(stores, trace_id=TRACE_ID):
    clock, lines = Clock(), []
    code = query.find(trace_id, URLS, 60.0, lines.append, stores, clock.sleep, clock.monotonic, lambda: 1700000000.0)
    return code, lines, clock


def test_all_three_found_prints_three_lines_and_exits_0():
    code, lines, clock = run_find(Stores())
    assert code == 0
    assert lines == [f"tempo: found {TRACE_ID}", f"loki: found {TRACE_ID}", f"prometheus: found {TRACE_ID}"]
    assert clock.sleeps == []


def test_a_trace_with_no_span_of_that_name_is_not_found():
    code, lines, _ = run_find(Stores(tempo=tempo_trace(("other",))))
    assert code == 1 and "tempo: not found (the trace has no span named ingest.alert)" in lines
    assert lines[-1] == "missing the trace id: tempo"


def test_a_trace_that_tempo_does_not_have_is_not_found_after_the_deadline():
    code, lines, clock = run_find(Stores(tempo=(404, b"")))
    assert code == 1 and "tempo: not found (HTTP 404)" in lines
    assert sum(clock.sleeps) >= 60  # it polled until its deadline, and no store waited for another's


def test_a_trace_without_a_log_line_names_loki():
    code, lines, _ = run_find(Stores(loki=loki_result(lines=0)))
    assert code == 1
    assert "loki: not found (no log line with this trace id)" in lines and lines[-1] == "missing the trace id: loki"


def test_a_log_line_whose_trace_id_is_a_label_not_structured_metadata_names_loki():
    code, lines, _ = run_find(Stores(loki=loki_result(stream={"service_name": "socboard", "trace_id": TRACE_ID}, metadata={})))
    assert code == 1
    assert "loki: not found (trace_id is a stream label, not structured metadata)" in lines
    assert lines[-1] == "missing the trace id: loki"


def test_a_log_line_that_matched_but_shows_no_metadata_is_not_accepted():
    code, lines, _ = run_find(Stores(loki=loki_result(metadata={})))
    assert code == 1 and "loki: not found (a log line matched but its structured metadata does not show the trace id)" in lines


def test_an_exemplar_with_another_trace_id_names_prometheus():
    code, lines, _ = run_find(Stores(prom=exemplars(OTHER_ID)))
    assert code == 1
    assert "prometheus: not found (the exemplars carry another trace id)" in lines
    assert lines[-1] == "missing the trace id: prometheus"


def test_no_exemplar_at_all_names_prometheus():
    code, lines, _ = run_find(Stores(prom=exemplars()))
    assert code == 1 and "prometheus: not found (no exemplar for the metric)" in lines


def test_every_store_that_lacks_the_id_is_named():
    code, lines, _ = run_find(Stores(tempo=(404, b""), loki=loki_result(lines=0), prom=exemplars(OTHER_ID)))
    assert code == 1 and lines[-1] == "missing the trace id: tempo, loki, prometheus"


def test_a_store_that_answers_late_is_found_on_a_later_poll():
    answers = [(404, b""), (404, b""), tempo_trace()]
    stores = Stores()
    first = stores.__call__

    def tempo_slowly(url, headers):
        if url.startswith("http://tempo/api/traces/"):
            return answers.pop(0) if len(answers) > 1 else answers[0]
        return first(url, headers)

    code, lines, clock = run_find(tempo_slowly)
    assert code == 0 and clock.sleeps == [2.0, 2.0]


def test_garbage_and_no_answer_are_not_found_not_a_crash():
    for tempo in [(200, b"not json"), (0, b""), (500, b"")]:
        code, lines, _ = run_find(Stores(tempo=tempo))
        assert code == 1 and lines[-1] == "missing the trace id: tempo"
    code, lines, _ = run_find(Stores(loki=(200, b'{"data": 3}')))
    assert code == 1 and "loki: not found (the answer has no result list)" in lines
    code, lines, _ = run_find(Stores(prom=(200, b"[]")))
    assert code == 1 and "prometheus: not found (the answer has no exemplar list)" in lines


@pytest.mark.parametrize("trace_id", ["", "abc", "0" * 32, TRACE_ID.upper(), TRACE_ID + "0", TRACE_ID[:-1] + "'", "x" * 32])
def test_a_bad_trace_id_exits_2_before_any_request(trace_id, capsys):
    stores = Stores()
    assert query.main(["find", trace_id], urls=URLS, get=stores) == 2
    assert stores.requests == [] and "refused" in capsys.readouterr().err


def test_the_loki_query_asks_for_the_trace_id_and_the_label_split():
    stores = Stores()
    run_find(stores)
    url, headers = next((u, h) for u, h in stores.requests if u.startswith("http://loki/"))
    params = parse_qs(urlsplit(url).query)
    assert params["query"] == [f'{{service_name="socboard"}} | trace_id="{TRACE_ID}"']
    assert headers == {"X-Loki-Response-Encoding-Flags": "categorize-labels"}
    tempo_url, tempo_headers = next((u, h) for u, h in stores.requests if u.startswith("http://tempo/"))
    assert tempo_url == f"http://tempo/api/traces/{TRACE_ID}" and tempo_headers == {"Accept": "application/json"}  # by id, not a search
    prom_url, _ = next((u, h) for u, h in stores.requests if u.startswith("http://prom/"))
    assert parse_qs(urlsplit(prom_url).query)["query"] == ["socboard_alert_time_to_queue_seconds_bucket"]


def test_ready_waits_for_each_store_and_names_the_late_ones():
    clock, lines = Clock(), []
    assert query.ready(URLS, 120.0, lines.append, Stores(), clock.sleep, clock.monotonic) == 0
    assert lines == ["tempo: ready", "loki: ready", "prometheus: ready", "alertmanager: ready", "grafana: ready", "collector: ready"]
    clock, lines = Clock(), []
    assert query.ready(URLS, 10.0, lines.append, Stores(ready=503), clock.sleep, clock.monotonic) == 1
    assert lines[-1] == "not ready in time: tempo, loki, prometheus, alertmanager, grafana, collector" and "tempo: not ready (HTTP 503)" in lines


def test_the_real_fetch_sends_the_urls_and_headers_and_reads_the_answer():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("X-Loki-Response-Encoding-Flags"), self.headers.get("Accept")))
            code, body = (404, b"") if "missing" in self.path else (200, b'{"ok": true}')
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert query.fetch(f"{base}/a?x=1", {"X-Loki-Response-Encoding-Flags": "categorize-labels", "Accept": "application/json"}) == (200, b'{"ok": true}')
        assert query.fetch(f"{base}/missing", {})[0] == 404
    finally:
        server.shutdown()
        server.server_close()
    assert query.fetch("http://127.0.0.1:9/", {}) == (0, b"")  # nothing listens on the discard port
    assert seen[0] == ("/a?x=1", "categorize-labels", "application/json")


def test_the_whole_cli_runs_with_defaults_for_the_urls_and_the_find_deadline(capsys):
    clock = Clock()
    code = query.main(["find", TRACE_ID], get=Stores_for_defaults(), sleep=clock.sleep, clock=clock.monotonic, wall=lambda: 1700000000.0)
    assert code == 0 and capsys.readouterr().out.count("found") == 3


def Stores_for_defaults():
    stores = Stores()

    def get(url, headers):
        return stores(url.replace("http://127.0.0.1:3200", "http://tempo").replace("http://127.0.0.1:3100", "http://loki").replace("http://127.0.0.1:9090", "http://prom"), headers)

    return get


# --- absent: the trace is in all three stores and the canary is in none (#292) --------------------------

CANARY = "CANARY-7f3a91c2d04e5b68"
class LeakStores(Stores):
    """Stores that also answer the leak checks; `leak` names the store whose answers hold the canary."""

    def __init__(self, leak=None, **answers):
        super().__init__(**answers)
        self.leak = leak

    def __call__(self, url, headers):
        status, body = super().__call__(url, headers)
        if url.startswith("http://tempo/api/traces/") and self.leak == "tempo":
            leaked = {"batches": [{"scopeSpans": [{"spans": [{"name": "ingest.alert", "attributes": [{"key": "socboard.canary", "value": {"stringValue": CANARY}}]}]}]}]}
            return 200, json.dumps(leaked).encode()
        if url.startswith("http://loki/") and self.leak == "loki" and "trace_id" not in url:  # the leak checks, not the find
            if "query_range" in url:
                return 200, json.dumps({"data": {"result": [{"stream": {"service_name": "socboard"}, "values": [["1", "x", {"structuredMetadata": {"socboard_canary": CANARY}}]]}]}}).encode()
            return 200, b'{"data": ["service_name", "socboard_canary"]}'
        if url.startswith("http://prom/api/v1/series") and self.leak == "prometheus":
            return 200, json.dumps({"data": [{"__name__": "m", "socboard_canary": CANARY}]}).encode()
        if url.startswith("http://prom/api/v1/labels"):
            return 200, b'{"data": ["__name__", "job"]}'
        if url.startswith("http://loki/loki/api/v1/labels"):
            return 200, b'{"data": ["service_name"]}'
        if url.startswith("http://prom/api/v1/series"):
            return 200, b'{"data": [{"__name__": "socboard_alert_time_to_queue_seconds_bucket", "job": "socboard"}]}'
        return status, body


def run_absent(stores, canary=CANARY, trace_id=TRACE_ID):
    clock, lines = Clock(), []
    code = query.absent(trace_id, canary, URLS, 60.0, lines.append, stores, clock.sleep, clock.monotonic, lambda: 1700000000.0)
    return code, lines


def test_no_canary_in_any_store_passes_after_the_trace_is_found():
    code, lines = run_absent(LeakStores())
    assert code == 0
    assert lines == [f"tempo: found {TRACE_ID}", f"loki: found {TRACE_ID}", f"prometheus: found {TRACE_ID}", "tempo: no canary", "loki: no canary", "prometheus: no canary"]


@pytest.mark.parametrize("store", ["tempo", "loki", "prometheus"])
def test_a_canary_in_one_store_fails_and_names_that_store(store):
    code, lines = run_absent(LeakStores(leak=store))
    assert code == 1
    assert lines[-1] == f"the canary reached: {store}"
    assert any(line.startswith(f"{store}: canary found") for line in lines)
    assert sum("no canary" in line for line in lines) == 2  # the other two stores are clean


def test_a_store_that_does_not_hold_the_trace_cannot_pass_the_canary_check():
    code, lines = run_absent(LeakStores(tempo=(404, b"")))
    assert code == 1 and "tempo: not found (HTTP 404)" in lines
    assert lines[-1] == "the canary check needs the trace in every store first"
    assert not any("no canary" in line for line in lines)  # a store with nothing to leak is not "clean"


def test_a_store_that_does_not_answer_the_leak_check_is_not_clean():
    stores = LeakStores()
    first = stores.__call__

    def fail_series(url, headers):
        return (500, b"") if url.startswith("http://prom/api/v1/series") else first(url, headers)

    code, lines = run_absent(fail_series)
    assert code == 1 and "prometheus: canary found (cannot tell (HTTP 500))" in lines


@pytest.mark.parametrize("canary", ["", "plain", "CANARY-", "CANARY-" + "x" * 61, "CANARY-a b", "CANARY-a'b", "canary-abc"])
def test_a_bad_canary_exits_2_before_any_request(canary, capsys):
    stores = LeakStores()
    assert query.main(["absent", TRACE_ID, canary], urls=URLS, get=stores) == 2
    assert stores.requests == [] and "refused" in capsys.readouterr().err


# --- grafana: what Grafana made of its provisioning (#293) -----------------------------------------------

TOKEN = "YWRtaW46c3ludGhldGljLXBhc3N3b3Jk"  # base64 of admin:synthetic-password


class GrafanaStores:
    """A fake Grafana: signed-in requests see the provisioned state, anonymous ones get 401."""

    def __init__(self, datasources=None, health=None, dashboard=None, anonymous=401):
        self.datasources = datasources if datasources is not None else [{"uid": "prometheus", "type": "prometheus"}, {"uid": "tempo", "type": "tempo"}, {"uid": "loki", "type": "loki"}]
        self.health = health or {}
        self.dashboard = dashboard if dashboard is not None else (200, json.dumps({"dashboard": {"panels": [{}] * 5}, "meta": {"provisioned": True}}).encode())
        self.anonymous, self.requests = anonymous, []

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        signed_in = headers.get("Authorization") == f"Basic {TOKEN}"
        path = url.removeprefix("http://graf")
        if path.startswith("/api/dashboards/uid/"):
            return self.dashboard if signed_in else (self.anonymous, b"")
        if not signed_in:
            return 401, b""
        if path == "/api/datasources":
            return 200, json.dumps(self.datasources).encode()
        for uid in ("prometheus", "tempo", "loki"):
            if path == f"/api/datasources/uid/{uid}/health":
                return self.health.get(uid, (200, b'{"status": "OK", "message": "ok"}'))
        return 404, b""


def run_grafana(stores, deadline=60.0):
    clock, lines = Clock(), []
    code = query.grafana("http://graf", TOKEN, deadline, lines.append, stores, clock.sleep, clock.monotonic)
    return code, lines, clock


def test_a_provisioned_grafana_passes_and_signs_in_with_basic_auth():
    stores = GrafanaStores()
    code, lines, clock = run_grafana(stores)
    assert code == 0 and clock.sleeps == []
    assert lines == ["grafana: the three datasources answer, the dashboard is provisioned with its five panels, and anonymous access is refused"]
    assert all(h == {"Authorization": f"Basic {TOKEN}"} for u, h in stores.requests if "datasources" in u)
    assert stores.requests[-1] == ("http://graf/api/dashboards/uid/socboard-queue-latency", {})  # the last request is the anonymous one


@pytest.mark.parametrize(
    ("stores", "expected"),
    [
        (GrafanaStores(datasources=[{"uid": "prometheus", "type": "prometheus"}, {"uid": "loki", "type": "loki"}]), "grafana: datasource tempo: missing or not of type tempo"),
        (GrafanaStores(datasources=[{"uid": "prometheus", "type": "prometheus"}, {"uid": "tempo", "type": "jaeger"}, {"uid": "loki", "type": "loki"}]), "grafana: datasource tempo: missing or not of type tempo"),
        (GrafanaStores(health={"loki": (200, b'{"status": "ERROR", "message": "no"}')}), "grafana: datasource loki: its health check does not say OK"),
        (GrafanaStores(health={"loki": (500, b"")}), "grafana: datasource loki: its health check does not say OK (HTTP 500)"),
        (GrafanaStores(dashboard=(404, b"")), "grafana: dashboard socboard-queue-latency: not found (HTTP 404)"),
        (GrafanaStores(dashboard=(200, json.dumps({"dashboard": {"panels": [{}] * 4}, "meta": {"provisioned": True}}).encode())), "grafana: dashboard socboard-queue-latency: 4 panels, not 5"),
        (GrafanaStores(dashboard=(200, json.dumps({"dashboard": {"panels": [{}] * 5}, "meta": {"provisioned": False}}).encode())), "grafana: dashboard socboard-queue-latency: not provisioned from a file"),
        (GrafanaStores(anonymous=200), "grafana: anonymous access: the dashboard answered HTTP 200 without a login, not 401"),
    ],
)
def test_each_refused_state_of_grafana_fails_and_says_which(stores, expected):
    code, lines, clock = run_grafana(stores, deadline=4.0)
    assert code == 1 and any(line.startswith(expected) for line in lines), lines
    assert sum(clock.sleeps) >= 4.0  # it polled to its deadline first, as the other checks do


def test_grafana_that_is_not_there_is_reported_not_crashed():
    code, lines, _ = run_grafana(lambda url, headers: (0, b""), deadline=2.0)
    assert code == 1 and lines == ["grafana: datasources: cannot list them (no answer)"]


def test_the_token_is_read_from_the_secrets_file_and_never_printed(tmp_path, capsys):
    secrets_file = tmp_path / "observability.secrets"
    secrets_file.write_text("OTHER=x\nSTACK_GRAFANA_ADMIN_PASSWORD=synthetic-password\n", encoding="utf-8")
    assert query.grafana_token(str(secrets_file)) == TOKEN
    assert query.grafana_token(str(tmp_path / "missing")) is None
    secrets_file.write_text("OTHER=x\n", encoding="utf-8")
    assert query.grafana_token(str(secrets_file)) is None
    assert query.main(["grafana", "--secrets", str(secrets_file)], urls={**URLS}, get=GrafanaStores()) == 2
    assert "refused" in capsys.readouterr().err
    secrets_file.write_text("STACK_GRAFANA_ADMIN_PASSWORD=synthetic-password\n", encoding="utf-8")
    clock = Clock()
    assert query.main(["grafana", "--secrets", str(secrets_file)], urls={**URLS}, get=GrafanaStores(), sleep=clock.sleep, clock=clock.monotonic) == 0
    out = capsys.readouterr().out
    assert "synthetic-password" not in out and TOKEN not in out
