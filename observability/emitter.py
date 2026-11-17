"""The walking skeleton's emitter (#340): one span, one log record and one histogram value, under one trace id.

    python observability/emitter.py --trace-id <32 lower-case hex> [--endpoint http://127.0.0.1:4318]

It stands in for the app, so the stack's checks do not wait for the app's telemetry (#289 to #291). It
sends, over OTLP/HTTP to a collector on the loopback, exactly what `observability/CONTRACT.md` names and
nothing else:

- a span `ingest.alert` (the only span attributes: `socboard.alert.severity`, `socboard.case.id`),
- one log record with the attribute `event` = `alert.ingested`, carrying the span's trace and span ids,
- one value of the histogram `socboard.alert.time_to_queue` (unit `s`, the contract's bucket bounds),
  recorded inside the span so the SDK keeps an exemplar with the trace id.

The resource is `service.name=socboard` and whatever `OTEL_RESOURCE_ATTRIBUTES` adds (the two
operator-set attributes). Every value is synthetic. The endpoint must be plain `http` on the loopback:
the emitter never sends anything off the machine.

Exit codes: 0 sent and flushed with no export problem, 1 an export problem (the SDK's own warning or
error about a failed export, for example a collector that is not there or refuses a request), 2 a refused
argument (before anything is created).
"""

import argparse
import logging
import re
import secrets
import sys
import time
from collections.abc import Sequence
from urllib.parse import urlsplit

# The contract's names (observability/CONTRACT.md, sections 1 to 4), fixed here so nothing a request
# carries can reach them. A change to a name is a change to the contract first.
SERVICE_NAME = "socboard"
SPAN_NAME = "ingest.alert"
SEVERITY_ATTRIBUTE = "socboard.alert.severity"
CASE_ID_ATTRIBUTE = "socboard.case.id"
HISTOGRAM_NAME = "socboard.alert.time_to_queue"
BUCKET_BOUNDS = (0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 30)  # seconds
EVENT_ATTRIBUTE = "event"
EVENT = "alert.ingested"
DEFAULT_ENDPOINT = "http://127.0.0.1:4318"
SYNTHETIC_SEVERITY = "High"  # one of Low, Medium, High, Critical
SYNTHETIC_CASE_ID = "1"  # decimal digits only
SYNTHETIC_DELAY = 1.5  # seconds, in the (1, 2] bucket
LOOPBACK = ("127.0.0.1", "localhost", "::1")
TRACE_ID = re.compile(r"[0-9a-f]{32}")


class Refused(Exception):
    pass


def check_trace_id(text: str) -> str:
    if not TRACE_ID.fullmatch(text) or set(text) == {"0"}:
        raise Refused("the trace id must be 32 lower-case hex characters and not all zeros")
    return text


def check_endpoint(text: str) -> str:
    """The collector's base URL: plain http on the loopback, no path, no credentials."""
    try:
        parts = urlsplit(text)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise Refused("the endpoint is not a valid URL") from None
    if parts.scheme != "http" or host not in LOOPBACK or parts.username or parts.password:
        raise Refused("the endpoint must be plain http on 127.0.0.1, localhost or ::1")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise Refused("the endpoint must have no path, query or fragment")
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}" if port else f"http://{shown}"


class ProblemCounter(logging.Handler):
    """Counts what the SDK says about a failed export: it logs and carries on, and the exit code must not."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


def emit(trace_id: str, endpoint: str, *, metric_reader=None, span_exporter=None, log_exporter=None) -> None:
    """Send the three signals and flush them. The keyword arguments let a test replace an exporter."""
    from opentelemetry import trace
    from opentelemetry._logs import SeverityNumber
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    # service.name is passed in code, so it wins over OTEL_SERVICE_NAME and OTEL_RESOURCE_ATTRIBUTES.
    resource = Resource.create({"service.name": SERVICE_NAME})
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter or OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(log_exporter or OTLPLogExporter(endpoint=f"{endpoint}/v1/logs")))
    reader = metric_reader or PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"))
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[reader],
        views=[View(instrument_name=HISTOGRAM_NAME, aggregation=ExplicitBucketHistogramAggregation(BUCKET_BOUNDS))],
    )
    histogram = meter_provider.get_meter(SERVICE_NAME).create_histogram(
        HISTOGRAM_NAME, unit="s", description="Seconds from an accepted ingest until the case is in the queue."
    )
    tracer = tracer_provider.get_tracer(SERVICE_NAME)
    logger = logger_provider.get_logger(SERVICE_NAME)

    # The chosen trace id is the remote parent's, so the span (and with it the log record and the
    # exemplar) carries it.
    parent = SpanContext(int(trace_id, 16), secrets.randbits(64) or 1, True, TraceFlags(TraceFlags.SAMPLED))
    parent_context = trace.set_span_in_context(NonRecordingSpan(parent))
    try:
        with tracer.start_as_current_span(
            SPAN_NAME,
            context=parent_context,
            attributes={SEVERITY_ATTRIBUTE: SYNTHETIC_SEVERITY, CASE_ID_ATTRIBUTE: SYNTHETIC_CASE_ID},
        ):
            logger.emit(
                timestamp=time.time_ns(),
                severity_number=SeverityNumber.INFO,
                severity_text="INFO",
                body=EVENT,
                attributes={EVENT_ATTRIBUTE: EVENT},
            )
            histogram.record(SYNTHETIC_DELAY)
    finally:
        # Each shutdown flushes: the span and the log record go out as they end, the metric at the end.
        meter_provider.shutdown()
        logger_provider.shutdown()
        tracer_provider.shutdown()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send one synthetic span, log record and histogram value.")
    parser.add_argument("--trace-id", required=True, help="32 lower-case hex characters")
    parser.add_argument("--endpoint", default=None, help=f"the collector's OTLP/HTTP base URL (default {DEFAULT_ENDPOINT})")
    args = parser.parse_args(argv)  # a bad argument exits 2
    try:
        trace_id = check_trace_id(args.trace_id)
        endpoint = check_endpoint(args.endpoint or DEFAULT_ENDPOINT)
    except Refused as refusal:
        print(f"observability emitter: refused: {refusal}", file=sys.stderr)
        return 2
    problems = ProblemCounter()
    sdk_log = logging.getLogger("opentelemetry")
    sdk_log.addHandler(problems)
    try:
        emit(trace_id, endpoint)
    finally:
        sdk_log.removeHandler(problems)
    if problems.count:
        print(f"emitter: {problems.count} export problem(s) reported by the SDK (the collector at {endpoint} did not take every signal)", file=sys.stderr)
        return 1
    print(f"emitter: sent trace {trace_id} to {endpoint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
