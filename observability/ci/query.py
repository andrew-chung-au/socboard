"""Ask Tempo, Loki and Prometheus whether one trace id reached them (#340).

    python observability/ci/query.py ready [--deadline 120]
    python observability/ci/query.py find <trace id> [--deadline 60]

`ready` polls the readiness endpoint of each store and of the collector from the runner (the collector
image has no shell for a container health check) and exits 1 naming each one that was not ready in time.

`find` asks, polling each store until its own deadline (60 seconds by default):

- Tempo: the trace by id (`/api/traces/<id>`, not a search), which must hold a span `ingest.alert`.
- Loki: `{service_name="socboard"} | trace_id="<id>"`, where `trace_id` must be structured metadata of
  the log line and not a stream label (a label would make a stream per trace). The request asks Loki
  to say which is which (`X-Loki-Response-Encoding-Flags: categorize-labels`; `VERIFY:` on the Loki
  version in the skeleton: when the run shows another shape, the check fails and says so).
- Prometheus: the exemplar API for `socboard_alert_time_to_queue_seconds_bucket`, where one exemplar
  must carry this trace id.

It prints one line per store, `found` or `not found (reason)`, and exits 1 naming each store that lacked
the id. Standard library only. The trace id is 32 lower-case hex characters (a refused one exits 2
before any request), so it cannot change a URL or a query. Nothing it sends or prints is secret.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence

TRACE_ID = re.compile(r"[0-9a-f]{32}")
SPAN_NAME = "ingest.alert"
EXEMPLAR_METRIC = "socboard_alert_time_to_queue_seconds_bucket"
LOKI_STREAM = '{service_name="socboard"}'
POLL_EVERY = 2.0  # seconds
REQUEST_TIMEOUT = 10  # seconds
WINDOW = 15 * 60  # seconds either side of now that a log line or exemplar may be found in

# Where the skeleton publishes each service, on the loopback (observability/ci/skeleton/compose.yaml).
DEFAULTS = {
    "tempo": "http://127.0.0.1:3200",
    "loki": "http://127.0.0.1:3100",
    "prometheus": "http://127.0.0.1:9090",
    "collector": "http://127.0.0.1:13133",
}
READY_PATHS = {"tempo": "/ready", "loki": "/ready", "prometheus": "/-/ready", "collector": "/"}

Fetch = Callable[[str, Mapping[str, str]], tuple[int, bytes]]  # (url, headers) -> (status, body); status 0 = no answer


def fetch(url: str, headers: Mapping[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # noqa: S310 (a loopback http URL)
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, OSError, ValueError):
        return 0, b""


def parse(body: bytes):
    try:
        return json.loads(body)
    except ValueError:
        return None


def names_in(node) -> set[str]:
    """Every string value under a key `name`, anywhere in a parsed JSON document."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "name" and isinstance(value, str):
                found.add(value)
            found |= names_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= names_in(item)
    return found


# Each check returns None when the id is found, else the reason it was not.


def check_tempo(get: Fetch, base: str, trace_id: str, now: float) -> str | None:
    status, body = get(f"{base}/api/traces/{trace_id}", {"Accept": "application/json"})
    if status == 0:
        return "no answer"
    if status != 200:
        return f"HTTP {status}"
    document = parse(body)
    if document is None:
        return "the answer is not JSON"
    if SPAN_NAME not in names_in(document):
        return f"the trace has no span named {SPAN_NAME}"
    return None


def check_loki(get: Fetch, base: str, trace_id: str, now: float) -> str | None:
    query = f'{LOKI_STREAM} | trace_id="{trace_id}"'
    params = {
        "query": query,
        "start": str(int((now - WINDOW) * 1e9)),
        "end": str(int((now + WINDOW) * 1e9)),
        "limit": "10",
        "direction": "backward",
    }
    status, body = get(
        f"{base}/loki/api/v1/query_range?{urllib.parse.urlencode(params)}",
        {"X-Loki-Response-Encoding-Flags": "categorize-labels"},
    )
    if status == 0:
        return "no answer"
    if status != 200:
        return f"HTTP {status}"
    document = parse(body)
    results = document.get("data", {}).get("result") if isinstance(document, dict) and isinstance(document.get("data"), dict) else None
    if not isinstance(results, list):
        return "the answer has no result list"
    as_label = as_metadata = lines = 0
    for stream in results:
        labels = stream.get("stream") if isinstance(stream, dict) else None
        for value in (stream.get("values") or []) if isinstance(stream, dict) else []:
            lines += 1
            if isinstance(labels, dict) and labels.get("trace_id") == trace_id:
                as_label += 1
            extra = value[2] if isinstance(value, list) and len(value) > 2 and isinstance(value[2], dict) else {}
            metadata = extra.get("structuredMetadata")
            if isinstance(metadata, dict) and metadata.get("trace_id") == trace_id:
                as_metadata += 1
    if as_label:
        return "trace_id is a stream label, not structured metadata"
    if as_metadata:
        return None
    if lines:
        return "a log line matched but its structured metadata does not show the trace id"
    return "no log line with this trace id"


def check_prometheus(get: Fetch, base: str, trace_id: str, now: float) -> str | None:
    params = {"query": EXEMPLAR_METRIC, "start": f"{now - WINDOW:.3f}", "end": f"{now + WINDOW:.3f}"}
    status, body = get(f"{base}/api/v1/query_exemplars?{urllib.parse.urlencode(params)}", {})
    if status == 0:
        return "no answer"
    if status != 200:
        return f"HTTP {status}"
    document = parse(body)
    series = document.get("data") if isinstance(document, dict) else None
    if not isinstance(series, list):
        return "the answer has no exemplar list"
    seen: set[str] = set()
    for each in series:
        for exemplar in (each.get("exemplars") or []) if isinstance(each, dict) else []:
            labels = exemplar.get("labels") if isinstance(exemplar, dict) else None
            if isinstance(labels, dict) and isinstance(labels.get("trace_id"), str):
                seen.add(labels["trace_id"])
    if trace_id in seen:
        return None
    if seen:
        return "the exemplars carry another trace id"
    return "no exemplar for the metric"


CHECKS = {"tempo": check_tempo, "loki": check_loki, "prometheus": check_prometheus}


def poll(attempt: Callable[[], str | None], deadline: float, sleep: Callable[[float], None], clock: Callable[[], float]) -> str | None:
    """None as soon as `attempt` finds the id, else the last reason once the deadline has passed."""
    end = clock() + deadline
    while True:
        reason = attempt()
        if reason is None:
            return None
        if clock() >= end:
            return reason
        sleep(POLL_EVERY)


def find(
    trace_id: str,
    urls: Mapping[str, str],
    deadline: float,
    say: Callable[[str], None],
    get: Fetch = fetch,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> int:
    missing = []
    for store, check in CHECKS.items():
        reason = poll(lambda: check(get, urls[store], trace_id, wall()), deadline, sleep, clock)
        if reason is None:
            say(f"{store}: found {trace_id}")
        else:
            say(f"{store}: not found ({reason})")
            missing.append(store)
    if missing:
        say("missing the trace id: " + ", ".join(missing))
        return 1
    return 0


def ready(
    urls: Mapping[str, str],
    deadline: float,
    say: Callable[[str], None],
    get: Fetch = fetch,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    late = []
    for name, path in READY_PATHS.items():
        def attempt(name=name, path=path) -> str | None:
            status, _ = get(f"{urls[name]}{path}", {})
            return None if status == 200 else ("no answer" if status == 0 else f"HTTP {status}")

        reason = poll(attempt, deadline, sleep, clock)
        say(f"{name}: ready" if reason is None else f"{name}: not ready ({reason})")
        if reason is not None:
            late.append(name)
    if late:
        say("not ready in time: " + ", ".join(late))
        return 1
    return 0


def positive(text: str) -> float:
    value = float(text)
    if not value > 0 or value == float("inf"):
        raise argparse.ArgumentTypeError("must be a number above 0")
    return value


def main(argv: Sequence[str] | None = None, **options) -> int:
    parser = argparse.ArgumentParser(description="Check that one trace id reached Tempo, Loki and Prometheus.")
    commands = parser.add_subparsers(dest="command", required=True)
    wait = commands.add_parser("ready", help="wait for the stores and the collector to answer")
    wait.add_argument("--deadline", type=positive, default=120.0, help="seconds to wait for each (default 120)")
    look = commands.add_parser("find", help="look for the trace id in the three stores")
    look.add_argument("trace_id", help="32 lower-case hex characters")
    look.add_argument("--deadline", type=positive, default=60.0, help="seconds to wait for each store (default 60)")
    args = parser.parse_args(argv)  # a bad argument exits 2
    say = lambda line: print(line, flush=True)  # noqa: E731
    urls = options.pop("urls", DEFAULTS)
    if args.command == "ready":
        return ready(urls, args.deadline, say, **options)
    if not TRACE_ID.fullmatch(args.trace_id) or set(args.trace_id) == {"0"}:
        print("observability query: refused: the trace id must be 32 lower-case hex characters and not all zeros", file=sys.stderr)
        return 2
    return find(args.trace_id, urls, args.deadline, say, **options)


if __name__ == "__main__":
    raise SystemExit(main())
