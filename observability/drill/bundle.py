"""The drill's incident bundle (#299): what the on-call agent reads once the hand-off has fired.

    python observability/drill/bundle.py --incident incident.json --environment ci --version 1.2.3 --out bundle

Writes into --out: `incident.json` (the hand-off's file, kept only when every value is one the hand-off
could have written), `p95.json` (the probe's 95th percentile over the last 15 minutes), `exemplars.json`
(the slowest exemplars: trace id, seconds, time) and, for each of those trace ids, `trace-<id>.json`
(span ids, names and times from Tempo, and log lines from Loki reduced to event, level, ids and time).

Nothing is copied from a store's answer. Each value is rebuilt from a fixed pattern or a fixed list of
words (CONTRACT.md sections 3 and 4) and anything else is dropped, so alert text, case text and a
planted canary cannot reach the bundle. The stores are asked through observability/ci/query.py.

Exit codes: 0 the files were written (a store that did not answer gives empty lists); 2 an incident
file that is not the hand-off's, or an unwritable folder. Standard library only.
"""

import argparse
import base64
import binascii
import importlib.util
import json
import math
import re
import sys
import time
import urllib.parse
from pathlib import Path

OBSERVABILITY = Path(__file__).resolve().parent.parent


def _load(name: str, *parts: str):
    spec = importlib.util.spec_from_file_location(name, OBSERVABILITY.joinpath(*parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


query = _load("drill_store_query", "ci", "query.py")
handoff = _load("drill_handoff", "oncall", "handoff.py")

P95_QUERY = "histogram_quantile(0.95, sum by (le) (rate(socboard_probe_time_to_queue_seconds_bucket[1m])))"
PROBE_EXEMPLARS = "socboard_probe_time_to_queue_seconds_bucket"
STEP = 15  # seconds between p95 points
SLOWEST = 5
SPAN_NAMES = ("POST /api/alerts", "ingest.alert", "queue.first_seen")  # CONTRACT.md section 3
EVENTS = ("alert.ingested", "alert.first_in_queue")  # section 4
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
REFUSED_WORDS = ("startsAt", "environment", "version", "dashboard_url", "summary", "owner", "extra_label", "extra_annotation")
HEX_SPAN = re.compile(r"[0-9a-f]{16}")
B64_SPAN = re.compile(r"[A-Za-z0-9+/]{11}=")  # Tempo's JSON may write an 8-byte id in base64
NANOS = re.compile(r"[0-9]{1,20}")
DASHBOARD = re.compile(r"https?://[^/?#@]+" + re.escape(handoff.DASHBOARD_PATH))


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _trace_id(value) -> str | None:
    ok = isinstance(value, str) and query.TRACE_ID.fullmatch(value) and set(value) != {"0"}
    return value if ok else None


def _span_id(value) -> str | None:
    if not isinstance(value, str):
        return None
    if HEX_SPAN.fullmatch(value):
        return value
    if B64_SPAN.fullmatch(value):
        try:
            return base64.b64decode(value, validate=True).hex()
        except (binascii.Error, ValueError):
            return None
    return None


def _nanos(value) -> int | None:
    text = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
    return int(text) if isinstance(text, str) and NANOS.fullmatch(text) else None


def clean_incident(document, environment: str, version: str) -> dict | None:
    """The hand-off's file reduced to values it could have written, or None when it is not that file.

    A value that fails is left out, as the hand-off does; `environment` and `version` must equal the
    values the run was started with, so a free-text value that matches a character rule is not enough.
    """
    if not isinstance(document, dict) or document.get("alertname") != handoff.ALERT_NAME:
        return None
    kept = {"alertname": handoff.ALERT_NAME}
    for name, text in (("summary", handoff.SUMMARY), ("owner", handoff.OWNER), ("query", handoff.QUERY),
                       ("environment", environment), ("version", version)):
        if document.get(name) == text:
            kept[name] = text
    started = document.get("startsAt")
    if isinstance(started, str) and handoff._time(started) == started:
        kept["startsAt"] = started
    url = document.get("dashboard_url")
    if isinstance(url, str) and DASHBOARD.fullmatch(url):
        kept["dashboard_url"] = url
    refused = document.get("refused")
    kept["refused"] = [word for word in refused if word in REFUSED_WORDS] if isinstance(refused, list) else []
    return kept


def p95(document) -> list[list[float]]:
    """[time, seconds] pairs from a Prometheus range answer; numbers only."""
    result = document.get("data", {}).get("result") if isinstance(document, dict) and isinstance(document.get("data"), dict) else None
    points = []
    for series in result if isinstance(result, list) else []:
        for pair in (series.get("values") or []) if isinstance(series, dict) else []:
            when, seconds = (_number(item) for item in pair[:2]) if isinstance(pair, list) and len(pair) >= 2 else (None, None)
            if when is not None and seconds is not None:
                points.append([round(when, 3), round(seconds, 3)])
    return sorted(points)


def slowest(document, count: int = SLOWEST) -> list[dict]:
    """The `count` exemplars with the largest value, one per trace id: {trace_id, seconds, time}."""
    series = document.get("data") if isinstance(document, dict) else None
    best: dict[str, dict] = {}
    for each in series if isinstance(series, list) else []:
        for exemplar in (each.get("exemplars") or []) if isinstance(each, dict) else []:
            labels = exemplar.get("labels") if isinstance(exemplar, dict) else None
            trace_id = _trace_id(labels.get("trace_id")) if isinstance(labels, dict) else None
            seconds, when = (_number(exemplar.get(key)) for key in ("value", "timestamp")) if trace_id else (None, None)
            if seconds is not None and when is not None and seconds >= best.get(trace_id, {"seconds": -1.0})["seconds"]:
                best[trace_id] = {"trace_id": trace_id, "seconds": round(seconds, 3), "time": round(when, 3)}
    return sorted(best.values(), key=lambda row: (-row["seconds"], row["trace_id"]))[:count]


def spans(document) -> list[dict]:
    """{span_id, parent_span_id (when there is one), name, start, end} for each span of a Tempo answer
    whose name is one of the contract's three; nothing else of a span is read."""
    rows: list[dict] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            row = _span(node) if "spanId" in node and "name" in node else None
            if row:
                rows.append(row)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    return sorted(rows, key=lambda row: (row["start"], row["span_id"]))


def _span(node: dict) -> dict | None:
    span_id, start, end = _span_id(node.get("spanId")), _nanos(node.get("startTimeUnixNano")), _nanos(node.get("endTimeUnixNano"))
    if node.get("name") not in SPAN_NAMES or None in (span_id, start, end):
        return None
    row = {"span_id": span_id, "name": node["name"], "start": start, "end": end}
    parent = _span_id(node.get("parentSpanId"))
    if parent:
        row["parent_span_id"] = parent
    return row


def logs(document, trace_id: str) -> list[dict]:
    """{time, level, event, trace_id, span_id} for each Loki line whose JSON has a contract event."""
    result = document.get("data", {}).get("result") if isinstance(document, dict) and isinstance(document.get("data"), dict) else None
    rows = []
    for stream in result if isinstance(result, list) else []:
        for value in (stream.get("values") or []) if isinstance(stream, dict) else []:
            when = _nanos(value[0]) if isinstance(value, list) and len(value) >= 2 else None
            try:
                line = json.loads(value[1]) if when is not None and isinstance(value[1], str) else None
            except ValueError:
                line = None
            if not isinstance(line, dict) or line.get("event") not in EVENTS or line.get("level") not in LEVELS:
                continue
            row = {"time": when, "level": line["level"], "event": line["event"], "trace_id": trace_id}
            span_id = _span_id(line.get("span_id"))
            if span_id:
                row["span_id"] = span_id
            rows.append(row)
    return sorted(rows, key=lambda row: row["time"])


def build(incident: dict, urls, get, now: float) -> dict[str, object]:
    """File name -> content for the bundle. `get(url, headers) -> (status, body)` is query.fetch's shape."""
    def answer(url: str, headers=None):
        status, body = get(url, headers or {})
        return query.parse(body) if status == 200 else None

    window = {"start": f"{now - query.WINDOW:.3f}", "end": f"{now:.3f}"}
    prom = urls["prometheus"]
    files: dict[str, object] = {"incident.json": incident}
    files["p95.json"] = p95(answer(f"{prom}/api/v1/query_range?" + urllib.parse.urlencode({"query": P95_QUERY, "step": str(STEP), **window})))
    exemplars = slowest(answer(f"{prom}/api/v1/query_exemplars?" + urllib.parse.urlencode({"query": PROBE_EXEMPLARS, **window})))
    files["exemplars.json"] = exemplars
    for row in exemplars:
        trace_id = row["trace_id"]
        loki = urllib.parse.urlencode({
            "query": f'{query.LOKI_STREAM} | trace_id="{trace_id}"',
            "start": str(int((now - query.WINDOW) * 1e9)),
            "end": str(int((now + 60) * 1e9)),
            "limit": "50",
            "direction": "forward",
        })
        files[f"trace-{trace_id}.json"] = {
            "trace_id": trace_id,
            "spans": spans(answer(f"{urls['tempo']}/api/traces/{trace_id}", {"Accept": "application/json"})),
            "logs": logs(answer(f"{urls['loki']}/loki/api/v1/query_range?{loki}", {"X-Loki-Response-Encoding-Flags": "categorize-labels"}), trace_id),
        }
    return files


def main(argv=None, *, get=query.fetch, wall=time.time, urls=query.DEFAULTS) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--incident", required=True, help="the hand-off's incident.json")
    parser.add_argument("--environment", required=True, help="the --environment the hand-off was given")
    parser.add_argument("--version", required=True, help="the --version the hand-off was given")
    parser.add_argument("--out", required=True, help="folder to write the bundle to")
    args = parser.parse_args(argv)
    try:
        raw = json.loads(Path(args.incident).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    incident = clean_incident(raw, args.environment, args.version)
    if incident is None:
        print("refused: the incident file is not one the hand-off wrote", file=sys.stderr)
        return 2
    files = build(incident, urls, get, wall())
    try:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (out / name).write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        print("cannot write the bundle folder", file=sys.stderr)
        return 2
    print(f"wrote {len(files)} files to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
