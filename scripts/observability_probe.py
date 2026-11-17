"""Synthetic probe (#294): ingest one synthetic alert, poll the queue, report the time to appear.

    .venv/bin/python scripts/observability_probe.py [--count N] [--interval S] <url>

Each cycle: sign in as `lead`, one queue read (so the app's watch rule sees a watched board), one
ingest with a new `traceparent` (sampled), then a queue read every second, with the same trace id,
until the case is returned or 30 seconds pass. The delay (ingest response to the read that returned
the case, monotonic clock, at most 30 seconds) goes to the recorder; one line is printed per cycle.
Then the probe claims, starts and closes its own case as the Lead with `false_positive`, so the
queue does not grow.

The measurement sits behind one seam, `Recorder = (delay_seconds, trace_id) -> None`. With
`OTEL_EXPORTER_OTLP_ENDPOINT` set, the recorder is the histogram `socboard.probe.time_to_queue`
(unit `s`, the bucket bounds of `observability/CONTRACT.md`), recorded with an exemplar that carries
the cycle's trace id, under the resource `service.name=socboard-probe` (plus whatever
`OTEL_RESOURCE_ATTRIBUTES` sets), and exported over OTLP at the end of the run. With the variable
unset or empty nothing is created: the default records nothing, and the OpenTelemetry packages are
not imported. The tests pass a fake, or the SDK's in-memory reader.

The URL is plain `http` on `localhost`, `127.0.0.1` or `::1`, or on one of the stack's service names
(`STACK_SERVICE_NAMES`, from `compose.yaml`, for a run inside the stack network), with no path. The
service names are a fixed list, not a setting: a variable could point the credentials at any host.
The key and the password come from `SOCBOARD_INGEST_API_KEY` and `SOCBOARD_SEED_PASSWORD_LEAD`, are
never printed, and no redirect is followed (`socboard.smoke.Client`).

Exit codes: 0 when every cycle's case appeared within 5 seconds (a rate-limited cycle has no case and
does not change the code), 1 when any was later, never appeared or a step failed, 2 for a refused
argument, URL or variable (before any request).
"""

import argparse
import math
import os
import secrets
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from socboard import smoke  # noqa: E402

STACK_SERVICE_NAMES = ("app", "proxy")  # the services in compose.yaml that serve the API
POLL_EVERY = 1.0  # seconds between queue reads
GIVE_UP = 30.0  # seconds; a case not seen by then is recorded as this (the top bucket bound)
ON_TIME = 5.0  # exit 0 only when every case appeared within this
DEFAULT_RETRY_WAIT = 60  # seconds, when a 429 has no usable Retry-After (the default ingest window)
MAX_RETRY_WAIT = 65

# Fixed text: the probe never sends real or user-provided text.
SYNTHETIC_ALERT = {
    "source": "observability-probe",
    "type": "failed_login",
    "asset": "probe-host",
    "title": "SYNTHETIC probe alert (observability-probe)",
    "description": "Synthetic alert from the observability probe; it can be ignored.",
}

Recorder = Callable[[float, str], None]

# The contract's names (observability/CONTRACT.md, sections 1 and 2): fixed here, so nothing a
# request carries can reach them.
PROBE_SERVICE_NAME = "socboard-probe"
HISTOGRAM_NAME = "socboard.probe.time_to_queue"
BUCKET_BOUNDS = (0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 30)  # seconds
ENDPOINT_VARIABLE = "OTEL_EXPORTER_OTLP_ENDPOINT"


def build_recorder(environ: Mapping[str, str], reader=None) -> tuple[Recorder | None, Callable[[], None]]:
    """(recorder, shutdown): the OTLP histogram when the endpoint variable is set, else (None, no-op).

    `reader` is a metric reader to use in place of the OTLP exporter's (the tests pass the SDK's
    in-memory reader). The exporter reads its endpoint and headers from the process environment, as
    the SDK does; the probe prints none of them. `shutdown` flushes what was recorded.
    """
    if reader is None and not (environ.get(ENDPOINT_VARIABLE) or "").strip():
        return None, lambda: None
    from opentelemetry import trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    if reader is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    # service.name is passed in code, so it wins over OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES.
    provider = MeterProvider(
        resource=Resource.create({"service.name": PROBE_SERVICE_NAME}),
        metric_readers=[reader],
        views=[View(instrument_name=HISTOGRAM_NAME, aggregation=ExplicitBucketHistogramAggregation(BUCKET_BOUNDS))],
    )
    histogram = provider.get_meter(PROBE_SERVICE_NAME).create_histogram(
        HISTOGRAM_NAME, unit="s", description="Seconds from a synthetic ingest until the case is in the queue."
    )

    def record(delay: float, trace_id: str) -> None:
        # The SDK's default exemplar filter keeps a measurement taken inside a sampled span context, so
        # the delay is recorded inside one that carries the cycle's trace id.
        context = SpanContext(int(trace_id, 16), secrets.randbits(64) or 1, False, TraceFlags(TraceFlags.SAMPLED))
        histogram.record(delay, context=trace.set_span_in_context(NonRecordingSpan(context)))

    return record, provider.shutdown


def parse_url(url: str) -> tuple[str, str, int | None, str]:
    """(scheme, host, port, prefix) this probe may send credentials to, or smoke.Refused."""
    checked, host, parts = url, None, None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        pass  # smoke.parse_base_url gives the refusal
    if host in STACK_SERVICE_NAMES and "@" not in parts.netloc and parts.netloc.lower().startswith(host):
        # smoke's rules (ASCII, no space, no query, no user name) apply to the URL with a loopback
        # host in place of the service name; the name is put back after.
        checked = urlunsplit(parts._replace(netloc="127.0.0.1" + parts.netloc[len(host) :]))
    scheme, checked_host, port, prefix = smoke.parse_base_url(checked)
    if scheme != "http":
        raise smoke.Refused("the URL scheme must be http")
    if prefix:
        raise smoke.Refused("the URL must have no path")
    return scheme, host if host in STACK_SERVICE_NAMES else checked_host, port, prefix


def retry_wait(text: str | None) -> int:
    """Seconds to wait after a 429: the header's whole number, within 1 to 65, else 60."""
    try:
        return min(max(int(text or ""), 1), MAX_RETRY_WAIT)
    except ValueError:
        return DEFAULT_RETRY_WAIT


def limited(client: smoke.Client) -> smoke.Client:
    client.deadline = time.monotonic() + smoke.REQUEST_TIMEOUT  # one request at a time, own limit
    return client


def read_queue(client: smoke.Client, token: str, trace: Mapping[str, str] | None = None) -> set[str]:
    """The ids in the queue, or smoke.StepFailed."""
    headers = {"Authorization": f"Bearer {token}", **(trace or {})}
    answer = limited(client).request("GET", "/api/queue", headers=headers)
    if answer.status != 200:
        raise smoke.StepFailed(smoke._error_text(answer))
    parsed = smoke._json(answer)
    cases = parsed.get("cases") if isinstance(parsed, dict) else None
    if not isinstance(cases, list):
        raise smoke.StepFailed("HTTP 200 without a queue")
    return {case["id"] for case in cases if isinstance(case, dict) and isinstance(case.get("id"), str)}


def close_case(client: smoke.Client, token: str, case_id: str) -> str | None:
    """Claim, start and close the case as the Lead; None when done, else the failing call and status."""
    path = f"/api/cases/{quote(case_id, safe='')}"
    calls = [
        ("claim", f"{path}/claim", None),
        ("start", f"{path}/transition", {"to": "Investigating"}),
        ("close", f"{path}/transition", {"to": "Closed", "closingReason": "false_positive"}),
    ]
    for name, url, body in calls:
        try:
            answer = limited(client).request("POST", url, headers={"Authorization": f"Bearer {token}"}, body=body)
        except smoke.StepFailed as failure:
            return f"{name}: {failure}"
        if answer.status != 200:
            return f"{name}: {smoke._error_text(answer)}"
    return None


def cycle(
    client: smoke.Client,
    password: str,
    key: str,
    record: Recorder,
    say: Callable[[str], None],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> tuple[int, int]:
    """One cycle. (exit code 0 or 1, seconds to wait before the next cycle or 0 for the interval)."""
    trace_id = secrets.token_hex(16)
    trace = {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"}
    token = smoke.step_login(limited(client), password)
    read_queue(client, token)  # the watch read: the app sees a watched board before the ingest
    alert = {**SYNTHETIC_ALERT, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    answer = limited(client).request("POST", "/api/alerts", headers={"X-API-Key": key, **trace}, body=alert)
    if answer.status == 429:
        wait = retry_wait(answer.retry_after)
        say(f"trace={trace_id} rate limited (retry after {wait}s)")
        return 0, wait
    if answer.status != 201:
        raise smoke.StepFailed(f"ingest: {smoke._error_text(answer)}")
    parsed = smoke._json(answer)
    case_id = parsed.get("id") if isinstance(parsed, dict) else None
    if not isinstance(case_id, str) or not case_id:
        raise smoke.StepFailed("ingest: HTTP 201 without a case id")
    started = clock()
    delay, seen = GIVE_UP, False
    while True:
        try:
            seen = case_id in read_queue(client, token, trace)
        except smoke.StepFailed:
            pass  # a failed read is a read that did not return the case; the user is still waiting
        if seen:
            delay = min(clock() - started, GIVE_UP)
            break
        sleep(POLL_EVERY)
        if clock() - started >= GIVE_UP:
            break
    record(delay, trace_id)
    say(f"trace={trace_id} case={case_id} delay={delay:.3f}" + ("" if seen else " not seen"))
    failure = close_case(client, token, case_id)
    if failure:
        say(f"trace={trace_id} case={case_id} close failed ({failure})")
    return (0 if seen and delay <= ON_TIME else 1), 0


def run(
    client: smoke.Client,
    password: str,
    key: str,
    count: int,
    interval: float,
    record: Recorder,
    say: Callable[[str], None],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> int:
    code = 0
    for number in range(count):
        began = clock()
        wait = 0
        try:
            result, wait = cycle(client, password, key, record, say, sleep, clock)
            code = max(code, result)
        except smoke.StepFailed as failure:
            say(f"cycle failed ({failure})")
            code = 1
            if "HTTP 401" in str(failure) or "HTTP 403" in str(failure):
                break  # a wrong key or password stays wrong; do not repeat it against the limits
        except Exception as error:  # a shape nothing expected: a line, never a traceback or a value
            say(f"cycle failed (unexpected {type(error).__name__})")
            code = 1
        if number < count - 1:
            sleep(wait or max(0.0, interval - (clock() - began)))
    return code


def non_negative(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be a number of 0 or more")
    return value


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def main(
    argv: Sequence[str] | None = None,
    environ: Mapping[str, str] | None = None,
    record: Recorder | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    parser = argparse.ArgumentParser(description="Probe a running SOCBoard with a synthetic alert.")
    parser.add_argument("url", help="http://127.0.0.1:<port>, or http://app:8000 inside the stack network")
    parser.add_argument("--count", type=positive_int, default=1, help="cycles to run (default 1)")
    parser.add_argument("--interval", type=non_negative, default=5.0, help="seconds between cycle starts (default 5)")
    args = parser.parse_args(argv)  # a bad argument exits 2
    source = os.environ if environ is None else environ
    try:
        scheme, host, port, prefix = parse_url(args.url)
        password, key = smoke.read_credentials(source)
    except smoke.Refused as refusal:
        print(f"observability probe: refused: {refusal}", file=sys.stderr)
        return 2
    shutdown = lambda: None  # noqa: E731
    if record is None:
        try:
            record, shutdown = build_recorder(source)
        except ImportError:
            print("observability probe: refused: OTEL_EXPORTER_OTLP_ENDPOINT is set but the OpenTelemetry packages are missing", file=sys.stderr)
            return 2
    client = smoke.Client(scheme, host, port, prefix, deadline=0.0)
    say = lambda line: print(line, flush=True)  # noqa: E731
    try:
        return run(client, password, key, args.count, args.interval, record or (lambda delay, trace_id: None), say, sleep, clock)
    finally:
        shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
