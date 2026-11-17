"""CI proof that one trace id is in the trace, the logs and the exemplars, and that no canary is stored (#295).

    python scripts/observability_check.py run [--url http://127.0.0.1:8000] [--secrets-file compose.secrets]
                                              [--report PATH] [--deadline 60]
    python scripts/observability_check.py collector-log FILE [--report PATH]

`run` (against the layered Compose stack, from observability/ci/check.sh):

1. chooses a random trace id and runs one cycle of the probe (scripts/observability_probe.py, #294) with
   it as the W3C `traceparent` of the ingest (the probe's `--trace-id` option). The probe signs in and
   reads the queue before the ingest, so the app's watch rule (#291: a queue read in the 6 seconds before
   the ingest) holds and the in-app histogram records;
2. runs the canary traffic with a second trace id: an alert whose title and description are the contract's
   canaries, a note holding the note canary, one failed sign-in with the password canary, and the
   ingest-key and token canaries in the headers of refused requests;
3. polls each store for up to `--deadline` seconds (60): Tempo by trace id (`/api/traces/<id>`, not a
   search) for the server span `POST /api/alerts` and `ingest.alert`; the `queue.first_seen` span starts
   its own trace (CONTRACT section 3), so it is found with one TraceQL search by span name and then read
   by id, and must link to this trace's `ingest.alert`; Loki for `{service_name="socboard"} |
   trace_id="<id>"` with `alert.ingested` and `alert.first_in_queue`, `trace_id` as structured metadata
   and not a label; Prometheus for an exemplar with the id on `socboard_alert_time_to_queue_seconds_bucket`
   and on `socboard_probe_time_to_queue_seconds_bucket`;
4. searches Tempo (every trace of the run), Loki (every line of the run's time range and the label
   names), Prometheus (series, label names, exemplars) for each canary of CONTRACT section 8.

`collector-log FILE` searches the collector's own log (check.sh saves `docker compose logs` of the
collector) for the canaries and adds the answer to the report. The log must hold a line of the collector
(the `docker compose logs` prefix), or the answer is "cannot tell".

`show [--report PATH]` prints the redacted report between two marker lines (the job log keeps the
evidence without an artifact) and, when `GITHUB_STEP_SUMMARY` is set, appends a table of the result lines.

A canary scan fails closed: an empty or cut answer (Loki at its line limit, a Tempo search at its limit,
a trace the search listed that cannot be read, no `socboard_*` series, no log line of either run trace id)
is "cannot tell", not "none found". Exemplars of every series are searched.

It prints the trace id, one line per signal, and for a canary only its kind, never its value. The report
(`--report`, default `.scratch/observability/check.json`) holds ids, the known span names, digit-only
times and the result lines, and no string copied from a store. The base URL
must be plain `http` on the loopback with no path, or the script exits 2 before any request. Standard
library only, apart from what the probe imports. Exit 0 when every check passed, 1 when one failed, 2 for
a refused argument.
"""

import argparse
import base64
import importlib.util
import json
import os
import re
import secrets
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from socboard import smoke  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The stores' queries and fetch are observability/ci/query.py's; the probe is the probe's.
query = _load("observability_ci_query", ROOT / "observability" / "ci" / "query.py")
probe = _load("observability_probe", ROOT / "scripts" / "observability_probe.py")
support = _load("observability_support", ROOT / "tests" / "observability_support.py")  # reads the canaries of CONTRACT section 8

SERVER_SPAN = "POST /api/alerts"
INGEST_SPAN = "ingest.alert"
QUEUE_SPAN = "queue.first_seen"
EVENTS = ("alert.ingested", "alert.first_in_queue")
ALERT_HISTOGRAM = query.EXEMPLAR_METRIC  # socboard_alert_time_to_queue_seconds_bucket
PROBE_HISTOGRAM = "socboard_probe_time_to_queue_seconds_bucket"
LOKI_HEADERS = {"X-Loki-Response-Encoding-Flags": "categorize-labels"}
LOG_LIMIT = 5000
TEMPO_LIMIT = 50
KNOWN_SPANS = (SERVER_SPAN, INGEST_SPAN, QUEUE_SPAN)
COLLECTOR_LINE = re.compile(r"^\S*otel-collector\S*\s+\|", re.MULTILINE)  # the prefix `docker compose logs` puts on each line
ALL_STREAMS = '{service_name=~".+"}'
ALL_SERIES = '{__name__=~".+"}'
DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_REPORT = ".scratch/observability/check.json"
SECRET_NAMES = (smoke.PASSWORD_VARIABLE, smoke.KEY_VARIABLE)

Fetch = query.Fetch


def refuse(message: str) -> int:
    print(f"observability check: refused: {message}", file=sys.stderr)
    return 2


def checked_base_url(url: str) -> tuple[str, str, int | None]:
    """(scheme, host, port) of a plain http loopback URL with no path, or smoke.Refused."""
    scheme, host, port, prefix = smoke.parse_base_url(url)
    if scheme != "http" or prefix:
        raise smoke.Refused("the URL must be plain http on the loopback with no path")
    return scheme, host, port


def store_urls(environ: Mapping[str, str]) -> dict[str, str]:
    """The overlay's loopback addresses; the ports follow its STACK_*_PORT variables."""
    urls = dict(query.DEFAULTS)
    for name, variable in (("tempo", "STACK_TEMPO_PORT"), ("loki", "STACK_LOKI_PORT"), ("prometheus", "STACK_PROMETHEUS_PORT")):
        port = environ.get(variable, "")
        if port.isdigit():
            urls[name] = f"http://127.0.0.1:{int(port)}"
    port = environ.get("STACK_OTLP_PORT", "")
    urls["otlp"] = f"http://127.0.0.1:{int(port) if port.isdigit() else 4318}"
    return urls


# ---- reading the stores' answers

def normal_id(value, size: int) -> str:
    """`value` as lower-case hex of `size` bytes: Tempo's JSON may give an id as hex or as base64."""
    if not isinstance(value, str):
        return ""
    if re.fullmatch(f"[0-9a-fA-F]{{{size * 2}}}", value):
        return value.lower()
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        return ""
    return raw.hex() if len(raw) == size else ""


def digits(value) -> str:
    """`value` when it is a number or a string of ASCII digits, else "": nothing else of a store's answer goes in the report."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,30}", value) else ""


def number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf") else None


def spans_of(document) -> list[dict]:
    """Every span (a mapping with a name and a spanId) anywhere in a Tempo answer."""
    found: list[dict] = []
    if isinstance(document, dict):
        if isinstance(document.get("name"), str) and "spanId" in document:
            found.append(document)
        for value in document.values():
            found += spans_of(value)
    elif isinstance(document, list):
        for item in document:
            found += spans_of(item)
    return found


def get_json(get: Fetch, url: str, headers: Mapping[str, str] | None = None):
    """(document, None) or (None, why not)."""
    status, body = get(url, headers or {})
    if status == 0:
        return None, "no answer"
    if status != 200:
        return None, f"HTTP {status}"
    document = query.parse(body)
    return (document, None) if document is not None else (None, "the answer is not JSON")


def tempo_trace(get: Fetch, base: str, trace_id: str):
    return get_json(get, f"{base}/api/traces/{trace_id}", {"Accept": "application/json"})


def tempo_search(get: Fetch, base: str, traql: str, start: float, end: float, limit: int = TEMPO_LIMIT):
    """(trace ids, None) or (None, why not)."""
    params = {"q": traql, "start": str(int(start)), "end": str(int(end) + 1), "limit": str(limit)}
    document, why = get_json(get, f"{base}/api/search?{urllib.parse.urlencode(params)}")
    if document is None:
        return None, why
    traces = document.get("traces") if isinstance(document, dict) else None
    if not isinstance(traces, list):
        return None, "the search answer has no trace list"
    return [normal_id(t.get("traceID"), 16) for t in traces if isinstance(t, dict) and normal_id(t.get("traceID"), 16)], None


def check_tempo(get: Fetch, base: str, trace_id: str, started: float, wall: float, seen: dict) -> str | None:
    """None when the trace holds the server span and ingest.alert, and a queue.first_seen links to it."""
    document, why = tempo_trace(get, base, trace_id)
    if document is None:
        return f"the trace is not in Tempo ({why})"
    spans = spans_of(document)
    by_name = {}
    for span in spans:
        by_name.setdefault(span["name"], span)
    server, ingest = by_name.get(SERVER_SPAN), by_name.get(INGEST_SPAN)
    if server is None:
        return f"the trace has no server span {SERVER_SPAN}"
    if ingest is None:
        return f"the trace has no span {INGEST_SPAN}"
    ingest_id = normal_id(ingest.get("spanId"), 8)
    if normal_id(ingest.get("parentSpanId"), 8) != normal_id(server.get("spanId"), 8):
        return f"{INGEST_SPAN} is not a child of the server span"
    seen["spans"] = [{"name": s["name"], "spanId": normal_id(s.get("spanId"), 8), "start": digits(s.get("startTimeUnixNano"))} for s in spans if s["name"] in KNOWN_SPANS]
    ids, why = tempo_search(get, base, f'{{ name = "{QUEUE_SPAN}" }}', started - 60, wall + 60)
    if ids is None:
        return f"no {QUEUE_SPAN} found ({why})"
    for other in ids:
        if other == trace_id:
            continue
        found, _ = tempo_trace(get, base, other)
        for span in spans_of(found) if found is not None else []:
            for link in span.get("links") or []:
                if (
                    span["name"] == QUEUE_SPAN
                    and isinstance(link, dict)
                    and normal_id(link.get("traceId"), 16) == trace_id
                    and normal_id(link.get("spanId"), 8) == ingest_id
                ):
                    seen["queue_first_seen"] = {"traceId": other, "spanId": normal_id(span.get("spanId"), 8)}
                    return None
    return f"no {QUEUE_SPAN} span links to {INGEST_SPAN} of this trace"


def loki_lines(get: Fetch, base: str, selector: str, started: float, wall: float, limit: int):
    """([(time ns, line, labels, metadata)], None) or (None, why not)."""
    params = {
        "query": selector,
        "start": str(int((started - 60) * 1e9)),
        "end": str(int((wall + 60) * 1e9)),
        "limit": str(limit),
        "direction": "forward",
    }
    document, why = get_json(get, f"{base}/loki/api/v1/query_range?{urllib.parse.urlencode(params)}", LOKI_HEADERS)
    if document is None:
        return None, why
    return loki_lines_of(document)


def loki_lines_of(document):
    """([(time ns, line, labels, metadata)], None) or (None, why not), from a query_range answer."""
    data = document.get("data") if isinstance(document, dict) else None
    results = data.get("result") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return None, "the answer has no result list"
    lines = []
    for stream in results:
        if not isinstance(stream, dict):
            continue
        for value in stream.get("values") or []:
            if isinstance(value, list) and len(value) >= 2:
                extra = value[2] if len(value) > 2 and isinstance(value[2], dict) else {}
                lines.append((str(value[0]), str(value[1]), stream.get("stream") or {}, extra.get("structuredMetadata") or {}))
    return lines, None


def check_loki(get: Fetch, base: str, trace_id: str, started: float, wall: float, seen: dict, events: Sequence[str] = EVENTS) -> str | None:
    lines, why = loki_lines(get, base, f'{query.LOKI_STREAM} | trace_id="{trace_id}"', started, wall, 100)
    if lines is None:
        return f"cannot read the log lines ({why})"
    if not lines:
        return "no log line with this trace id"
    if any(isinstance(labels, dict) and "trace_id" in labels for _, _, labels, _ in lines):
        return "trace_id is a stream label, not structured metadata"
    held = [(ts, line, meta) for ts, line, _, meta in lines if isinstance(meta, dict) and meta.get("trace_id") == trace_id]
    if not held:
        return "the log lines do not show the trace id as structured metadata"
    found = {}
    for ts, line, meta in held:
        for event in events:
            if event == meta.get("event") or event in line:
                found.setdefault(event, ts)
    missing = [event for event in events if event not in found]
    if missing:
        return "no log line " + " or ".join(missing) + " with this trace id"
    seen["lines"] = [{"time": digits(ts), "event": event, "traceId": trace_id} for event, ts in found.items()]
    return None


def exemplar_ids(get: Fetch, base: str, metric: str, started: float, wall: float):
    """({trace id: time}, None) or (None, why not)."""
    params = {"query": metric, "start": f"{started - 60:.3f}", "end": f"{wall + 60:.3f}"}
    document, why = get_json(get, f"{base}/api/v1/query_exemplars?{urllib.parse.urlencode(params)}")
    if document is None:
        return None, why
    series = document.get("data") if isinstance(document, dict) else None
    if not isinstance(series, list):
        return None, "the answer has no exemplar list"
    ids = {}
    for each in series:
        for exemplar in (each.get("exemplars") or []) if isinstance(each, dict) else []:
            labels = exemplar.get("labels") if isinstance(exemplar, dict) else None
            if isinstance(labels, dict) and isinstance(labels.get("trace_id"), str):
                ids[labels["trace_id"]] = number(exemplar.get("timestamp"))
    return ids, None


def check_exemplar(get: Fetch, base: str, metric: str, trace_id: str, started: float, wall: float, seen: dict) -> str | None:
    ids, why = exemplar_ids(get, base, metric, started, wall)
    if ids is None:
        return f"cannot read the exemplars ({why})"
    if trace_id in ids:
        seen[metric] = {"traceId": trace_id, "time": ids[trace_id]}
        return None
    return "its exemplars carry another trace id" if ids else "no exemplar"


# ---- the canary search. Each returns the kinds found in what the store answers, or (None, why not).

def kinds_in(body: bytes, canaries: Mapping[str, str]) -> list[str]:
    lowered = body.lower()
    return [kind for kind, value in canaries.items() if value.lower().encode() in lowered]


def scan_tempo(get, base, ids, canaries, started, wall):
    bodies = []
    wanted = list(dict.fromkeys(ids))
    found, why = tempo_search(get, base, '{ resource.service.name = "socboard" }', started - 60, wall + 60)
    if found is None:
        return None, f"cannot search ({why})"
    if len(found) >= TEMPO_LIMIT:
        return None, f"cannot tell: the search reached its limit of {TEMPO_LIMIT} traces"
    wanted += [each for each in found if each not in wanted]
    for each in wanted:
        status, body = get(f"{base}/api/traces/{each}", {"Accept": "application/json"})
        if status != 200:
            what = "a trace of the run" if each in ids else "a trace the search listed"
            return None, f"cannot read {what} (HTTP {status})" if status else f"cannot read {what} (no answer)"
        bodies.append(body)
    return sorted({kind for body in bodies for kind in kinds_in(body, canaries)}), None


def scan_loki(get, base, canaries, started, wall, run_ids: Sequence[str] = ()):
    """The kinds found in every line of the window (newest first, so a cut drops the oldest) and the label names.

    Fails closed ("cannot tell") when the answer reaches LOG_LIMIT lines, and when its lines do not
    include each trace id of the run: an empty or partial answer proves nothing.
    """
    params = {"start": str(int((started - 60) * 1e9)), "end": str(int((wall + 60) * 1e9))}
    every_line = {**params, "query": ALL_STREAMS, "limit": str(LOG_LIMIT), "direction": "backward"}
    found: set[str] = set()
    lines = None
    for path in (f"/loki/api/v1/query_range?{urllib.parse.urlencode(every_line)}", f"/loki/api/v1/labels?{urllib.parse.urlencode(params)}"):
        status, body = get(f"{base}{path}", LOKI_HEADERS)
        if status != 200:
            return None, f"cannot read (HTTP {status})" if status else "cannot read (no answer)"
        found |= set(kinds_in(body, canaries))
        if lines is None:
            document = query.parse(body)
            lines, why = loki_lines_of(document) if document is not None else (None, "the answer is not JSON")
            if lines is None:
                return None, f"cannot read the log lines ({why})"
    if found:
        return sorted(found), None
    if len(lines) >= LOG_LIMIT:
        return None, f"cannot tell: the answer reached its limit of {LOG_LIMIT} lines"
    absent = [each for each in dict.fromkeys(run_ids) if not any(each in line or each in str(labels) or each in str(meta) for _, line, labels, meta in lines)]
    if absent:
        return None, f"cannot tell: the answer holds no line of {len(absent)} of the run's {len(set(run_ids))} trace ids"
    return [], None


def scan_prometheus(get, base, canaries, started, wall):
    """The kinds found in the series, the label names and the exemplars of every series.

    Fails closed unless the series answer holds a `socboard_*` series.
    """
    window = {"start": f"{started - 60:.3f}", "end": f"{wall + 60:.3f}"}
    series_path = f"/api/v1/series?{urllib.parse.urlencode({**window, 'match[]': ALL_SERIES})}"
    paths = [series_path, f"/api/v1/labels?{urllib.parse.urlencode(window)}"]
    paths += [f"/api/v1/query_exemplars?{urllib.parse.urlencode({**window, 'query': metric})}" for metric in (ALL_SERIES, ALERT_HISTOGRAM, PROBE_HISTOGRAM)]
    found: set[str] = set()
    named = False
    for path in paths:
        status, body = get(f"{base}{path}", {})
        if status != 200:
            return None, f"cannot read (HTTP {status})" if status else "cannot read (no answer)"
        found |= set(kinds_in(body, canaries))
        if path == series_path:
            document = query.parse(body)
            data = document.get("data") if isinstance(document, dict) else None
            named = isinstance(data, list) and any(isinstance(each, dict) and str(each.get("__name__", "")).startswith("socboard_") for each in data)
    if found:
        return sorted(found), None
    if not named:
        return None, "cannot tell: the series answer holds no socboard_* series"
    return [], None


# ---- the traffic

def canary_traffic(url: str, password: str, key: str, canaries: Mapping[str, str], trace_id: str) -> list[str]:
    """Send the canaries; the problems (steps that did not answer as expected), never a value."""
    scheme, host, port = checked_base_url(url)
    client = smoke.Client(scheme, host, port, "", deadline=0.0)
    trace = {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"}
    problems: list[str] = []

    def send(step: str, expected: int, method: str, path: str, headers=None, body=None):
        client.deadline = time.monotonic() + smoke.REQUEST_TIMEOUT
        try:
            answer = client.request(method, path, headers={**trace, **(headers or {})}, body=body)
        except smoke.StepFailed as failure:
            problems.append(f"{step}: {failure}")
            return None
        if answer.status != expected:
            problems.append(f"{step}: {smoke._error_text(answer)}, expected HTTP {expected}")
            return None
        return answer

    client.deadline = time.monotonic() + smoke.REQUEST_TIMEOUT
    try:
        token = smoke.step_login(client, password)
    except smoke.StepFailed as failure:
        return [f"sign-in: {failure}"]
    alert = {**smoke.SYNTHETIC_ALERT, "title": canaries["alert-title"], "description": canaries["alert-description"], "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    answer = send("ingest of the canary alert", 201, "POST", "/api/alerts", {"X-API-Key": key}, alert)
    parsed = smoke._json(answer) if answer is not None else None
    case_id = parsed.get("id") if isinstance(parsed, dict) else None
    if isinstance(case_id, str) and case_id:
        send("note", 201, "POST", f"/api/cases/{urllib.parse.quote(case_id, safe='')}/notes", {"Authorization": f"Bearer {token}"}, {"text": canaries["case-note"]})
    elif answer is not None:
        problems.append("note: the ingest answer has no case id, so the note was not sent")
    send("failed sign-in", 401, "POST", "/api/login", None, {"username": smoke.LEAD_USERNAME, "password": canaries["password"]})
    refused = {**alert, "title": "SYNTHETIC refused alert", "description": "Refused: wrong key.", "asset": canaries.get("account-number", "probe-host")}
    send("ingest with a wrong key", 401, "POST", "/api/alerts", {"X-API-Key": canaries["ingest-key"]}, refused)
    send("queue with a wrong token", 401, "GET", "/api/queue", {"Authorization": f"Bearer {canaries['token']}"})
    return problems


def run_probe(url: str, password: str, key: str, otlp: str, trace_id: str) -> int:
    """One probe cycle. The OTLP exporter reads the process environment, so for the run the endpoint is
    the loopback store URL whatever the caller's environment says, and every other OTEL_EXPORTER_OTLP_*
    variable (headers, a metrics-only endpoint, ...) is removed. The environment is restored after."""
    saved = dict(os.environ)
    try:
        for name in [name for name in os.environ if name.startswith("OTEL_EXPORTER_OTLP_")]:
            del os.environ[name]
        os.environ[probe.ENDPOINT_VARIABLE] = otlp
        environ = {smoke.PASSWORD_VARIABLE: password, smoke.KEY_VARIABLE: key, probe.ENDPOINT_VARIABLE: otlp}
        return probe.main([url, "--trace-id", trace_id], environ)
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ---- the run

def check(
    trace_id: str,
    canary_trace_id: str,
    urls: Mapping[str, str],
    canaries: Mapping[str, str],
    deadline: float,
    say: Callable[[str], None],
    drive_probe: Callable[[str], int],
    drive_canaries: Callable[[str], list[str]],
    get: Fetch = query.fetch,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> tuple[int, dict]:
    """(exit code, redacted report). The report holds ids, names and times only."""
    report: dict = {"traceId": trace_id, "canaryTraceId": canary_trace_id, "startedAt": 0.0}
    failed: list[str] = []
    started = report["startedAt"] = wall()
    say(f"trace id: {trace_id}")
    code = drive_probe(trace_id)
    say(f"probe: one cycle, exit {code}" + ("" if code == 0 else " (a slow or failed cycle; the lookups below say which signal is missing)"))
    if code == 2:
        return 2, report
    problems = drive_canaries(canary_trace_id)
    for problem in problems:
        say(f"FAIL canary traffic: {problem}")
        failed.append("canary traffic")

    def poll(signal: str, attempt: Callable[[], str | None], ok: str) -> None:
        reason = query.poll(attempt, deadline, sleep, clock)
        if reason is None:
            say(f"{signal}: found {ok}")
        else:
            say(f"FAIL {signal}: the trace id {trace_id} is missing: {reason}")
            failed.append(signal)

    tempo, loki, alerts, probes = {}, {}, {}, {}
    poll("tempo", lambda: check_tempo(get, urls["tempo"], trace_id, started, wall(), tempo), f"{SERVER_SPAN}, {INGEST_SPAN} and a linked {QUEUE_SPAN}")
    poll("loki", lambda: check_loki(get, urls["loki"], trace_id, started, wall(), loki), " and ".join(EVENTS) + " with trace_id as structured metadata")
    poll("prometheus " + ALERT_HISTOGRAM, lambda: check_exemplar(get, urls["prometheus"], ALERT_HISTOGRAM, trace_id, started, wall(), alerts), "an exemplar")
    poll("prometheus " + PROBE_HISTOGRAM, lambda: check_exemplar(get, urls["prometheus"], PROBE_HISTOGRAM, trace_id, started, wall(), probes), "an exemplar")
    report["tempo"], report["loki"] = tempo, loki
    report["prometheus"] = {"exemplars": {**alerts, **probes}}

    # The canary search is only worth anything once the canary run's own signals are stored.
    held, canary_lines = {}, {}
    reasons = [
        query.poll(lambda: check_tempo_canary_trace(get, urls["tempo"], canary_trace_id), deadline, sleep, clock),
        query.poll(lambda: check_loki(get, urls["loki"], canary_trace_id, started, wall(), canary_lines, EVENTS[:1]), deadline, sleep, clock),
    ]
    if any(reasons):
        say("FAIL canary search: the canary run's own trace or log line did not reach the stores, so a search would prove nothing")
        failed.append("canary search")
    tempo_ids = [trace_id, canary_trace_id] + ([tempo["queue_first_seen"]["traceId"]] if "queue_first_seen" in tempo else [])
    scans = {
        "tempo": scan_tempo(get, urls["tempo"], tempo_ids, canaries, started, wall()),
        "loki": scan_loki(get, urls["loki"], canaries, started, wall(), [trace_id, canary_trace_id]),
        "prometheus": scan_prometheus(get, urls["prometheus"], canaries, started, wall()),
    }
    for store, (kinds, why) in scans.items():
        if kinds is None:
            say(f"FAIL canary {store}: {why}")
            failed.append(f"canary {store}")
        elif kinds:
            say(f"FAIL canary {store}: found canary kinds: {', '.join(kinds)}")
            failed.append(f"canary {store}")
        else:
            say(f"canary {store}: none of the {len(canaries)} canary kinds found")
        held[store] = {"found": kinds if kinds is not None else "cannot tell"}
    report["canary"] = {"kindsSearched": sorted(canaries), "stores": held}
    if failed:
        say("FAILED: " + ", ".join(dict.fromkeys(failed)))
        return 1, report
    return 0, report


def check_tempo_canary_trace(get: Fetch, base: str, trace_id: str) -> str | None:
    document, why = tempo_trace(get, base, trace_id)
    if document is None:
        return why
    return None if INGEST_SPAN in {span["name"] for span in spans_of(document)} else f"no {INGEST_SPAN}"


def scan_collector_log(path: Path, canaries: Mapping[str, str]) -> tuple[list[str] | None, str]:
    try:
        body = path.read_bytes()
    except OSError:
        return None, "the collector log cannot be read"
    if not body.strip():
        return None, "the collector log is empty, so it proves nothing"
    if not COLLECTOR_LINE.search(body.decode("utf-8", "replace")):
        return None, "the collector log holds no line of the collector (an error text, not its log), so it proves nothing"
    return kinds_in(body, canaries), ""


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_secrets(path: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """The probe's two credentials: from the environment, else from `NAME=value` lines of the file."""
    values = {name: environ[name] for name in SECRET_NAMES if environ.get(name)}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if separator and name in SECRET_NAMES and name not in values:
            values[name] = value
    return values


BEGIN_MARK = "----- observability report (redacted) begin -----"
END_MARK = "----- observability report (redacted) end -----"


def summary_table(results: Sequence) -> str:
    rows = ["### Observability proof (#295)", "", "| Result | Line |", "| --- | --- |"]
    for line in (str(each) for each in results):
        rows.append(f"| {'FAIL' if line.startswith('FAIL') else 'ok'} | {line.replace('|', '/')} |")
    return "\n".join(rows) + "\n"


def show_report(path: Path, canaries: Mapping[str, str], summary: str | None, say: Callable[[str], None]) -> int:
    """Print the redacted report JSON between marker lines, and append a result table to the step summary.

    A report that holds a canary is not printed (only the kinds are named): the report is meant to be
    read in a job log, so the log gets the same check as the stores.
    """
    try:
        text = path.read_text(encoding="utf-8")
        report = json.loads(text)
    except (OSError, ValueError):
        say(f"report: none at {path}, nothing to print")
        return 0
    kinds = kinds_in(text.encode("utf-8"), canaries)
    if kinds:
        say(f"FAIL report holds canary kinds: {', '.join(kinds)}; it is not printed")
        return 1
    say(BEGIN_MARK)
    say(text.rstrip("\n"))
    say(END_MARK)
    results = report.get("results") if isinstance(report, dict) else None
    if summary and isinstance(results, list):
        try:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(summary_table(results))
        except OSError:
            say("report: the step summary cannot be written")
    return 0


def positive(text: str) -> float:
    value = float(text)
    if not value > 0 or value == float("inf"):
        raise argparse.ArgumentTypeError("must be a number above 0")
    return value


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None, **options) -> int:
    parser = argparse.ArgumentParser(description="Prove that one trace id is in the trace, the logs and the exemplars, and that no canary is stored.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="drive the probe and the canary traffic, then read the stores")
    run.add_argument("--url", default=DEFAULT_URL, help=f"the app, plain http on the loopback (default {DEFAULT_URL})")
    run.add_argument("--secrets-file", default="compose.secrets", help="NAME=value lines holding the ingest key and the Lead's password (the environment wins)")
    run.add_argument("--deadline", type=positive, default=60.0, help="seconds to wait for each signal (default 60)")
    run.add_argument("--report", default=DEFAULT_REPORT)
    log = commands.add_parser("collector-log", help="search the collector's log file for the canaries")
    log.add_argument("file")
    log.add_argument("--report", default=DEFAULT_REPORT)
    show = commands.add_parser("show", help="print the redacted report between marker lines, and a result table to $GITHUB_STEP_SUMMARY when it is set")
    show.add_argument("--report", default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    source = os.environ if environ is None else environ
    say = options.pop("say", lambda line: print(line, flush=True))
    canaries = support.canaries()
    if args.command == "show":
        return show_report(Path(args.report), canaries, source.get("GITHUB_STEP_SUMMARY"), say)
    if args.command == "collector-log":
        results: list[str] = []
        kinds, why = scan_collector_log(Path(args.file), canaries)
        if kinds is None:
            results.append(f"FAIL canary collector log: {why}")
        elif kinds:
            results.append(f"FAIL canary collector log: found canary kinds: {', '.join(kinds)}")
        else:
            results.append(f"canary collector log: none of the {len(canaries)} canary kinds found")
        say(results[0])
        report_path = Path(args.report)
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report.setdefault("canary", {}).setdefault("stores", {})["collector"] = {"found": kinds if kinds is not None else "cannot tell"}
            report.setdefault("results", []).extend(results)
            write_report(report_path, report)
        return 0 if kinds == [] else 1
    try:
        checked_base_url(args.url)
        values = read_secrets(Path(args.secrets_file), source)
        password, key = smoke.read_credentials(values)
    except smoke.Refused as refusal:
        return refuse(str(refusal))
    urls = store_urls(source)
    drive_probe = options.pop("drive_probe", lambda trace_id: run_probe(args.url, password, key, urls["otlp"], trace_id))
    drive_canaries = options.pop("drive_canaries", lambda trace_id: canary_traffic(args.url, password, key, canaries, trace_id))
    results: list[str] = []

    def say_and_keep(line: str) -> None:
        results.append(line)
        say(line)

    code, report = check(secrets.token_hex(16), secrets.token_hex(16), urls, canaries, args.deadline, say_and_keep, drive_probe, drive_canaries, **options)
    report["results"] = list(results)
    if code != 2:
        write_report(Path(args.report), report)
        say(f"report: {args.report}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
