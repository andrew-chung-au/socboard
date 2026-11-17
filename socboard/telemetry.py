"""Traces, metrics and JSON logs from the environment (#289). Off unless an endpoint is set.

`observability/CONTRACT.md` is the source of every name, label and field here.

`setup(environ)` is called once from `create_app` and returns the function that flushes and shuts
everything down (the lifespan calls it). With `OTEL_EXPORTER_OTLP_ENDPOINT` unset or empty nothing is
created: no provider, exporter, handler or thread, and every helper does nothing. With it set:

- The resource is `service.name=socboard` (fixed in code) plus `deployment.environment.name` and
  `service.version` read from `OTEL_RESOURCE_ATTRIBUTES`. A missing one, or one that is not 1 to 64 characters of `A-Z a-z 0-9 . - _ +`, is left out and named (never its value) in one warning.
- The three providers (tracer, meter, logger) are held in this module and used through their own
  objects. None is installed as the process-wide provider, because the SDK allows that once per
  process; so a test can set up and shut down as often as it likes and nothing leaks between tests.
- Export is OTLP over HTTP from background threads (batching processors and a periodic reader), each
  request with a short timeout (`EXPORT_TIMEOUT_SECONDS`), so an unreachable collector never holds a
  request or `create_app`. The tracer uses an always-on sampler, not a parent-based one.
- Logs: the `socboard` logger tree gets a JSON handler on standard output and an OTLP handler. They
  are never put on the root logger or on `opentelemetry.*`, so an export failure the SDK logs cannot
  loop back into the exporter. Both are allow-lists: a record's arguments and extra fields are never
  rendered, and an exception is shown as its type only (the JSON line has no exception field).
  The `event` field is the record's message template, unformatted.

The SDK's logs API is experimental (its module names start with an underscore); only this module
imports it. The exporters can be passed in (`span_exporter`, `metric_exporter`, `log_exporter`) so
tests use the SDK's in-memory ones; the OTLP exporters are then not created.
"""

import contextlib
import json
import logging
import os
import re
import sys
import threading
import time
import warnings
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import IO, Protocol
from urllib.parse import unquote

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.metrics import Counter, Histogram
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ENDPOINT_VARIABLE = "OTEL_EXPORTER_OTLP_ENDPOINT"
RESOURCE_VARIABLE = "OTEL_RESOURCE_ATTRIBUTES"
EXPORT_TIMEOUT_SECONDS = 3.0
PENDING_MAX = 10_000  # entries of the ingest-to-queue map; the oldest is dropped first
PENDING_MAX_AGE_SECONDS = 600.0
WATCH_SECONDS = 6.0  # two of the client's 3-second polls (#338)

# The contract's names (observability/CONTRACT.md): fixed here, so nothing a request carries can reach them.
SERVICE_NAME = "socboard"
OPERATOR_ATTRIBUTES = ("deployment.environment.name", "service.version")
OPERATOR_VALUE = re.compile(r"[A-Za-z0-9._+-]{1,64}")  # used with fullmatch, so a trailing newline is refused
INGEST_COUNTER = "socboard.ingest.alerts"
OUTCOMES = ("accepted", "refused")
TIME_TO_QUEUE_HISTOGRAM = "socboard.alert.time_to_queue"
BUCKET_BOUNDS = (0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 30)  # seconds
# span name -> (kind, starts its own trace)
SPANS = {
    "POST /api/alerts": (SpanKind.SERVER, False),
    "ingest.alert": (SpanKind.INTERNAL, False),
    "queue.first_seen": (SpanKind.INTERNAL, True),
}
SEVERITY_ATTRIBUTE = "socboard.alert.severity"
CASE_ID_ATTRIBUTE = "socboard.case.id"
SEVERITIES = ("Low", "Medium", "High", "Critical")
# "alert.refused" is not in the contract's event table yet (#290 spec note): it is the one fixed event of a refused ingest.
EVENTS = ("alert.ingested", "alert.first_in_queue", "alert.refused")
EVENT_LOGGER = "socboard.events"
INGEST_PATH = "/api/alerts"
SERVER_SPAN = "POST /api/alerts"
REFUSED_STATUSES = (401, 422, 429)  # wrong key, invalid body, rate limit
_TRACE_HEADERS = (b"traceparent", b"tracestate")  # the only request headers telemetry ever reads

class CaseLike(Protocol):
    id: str
    severity: str


_log = logging.getLogger("socboard.telemetry")
_propagator = TraceContextTextMapPropagator()


def _nothing() -> None:
    return None


class _Runtime:
    def __init__(self, tracer: trace.Tracer, counter: Counter, histogram: Histogram, clock: Callable[[], float]) -> None:
        self.tracer = tracer
        self.counter = counter
        self.histogram = histogram
        self.clock = clock
        # The queue-read measure (#291). Assumes one worker (as the app's limiters do) and loses the
        # entries on a restart, so a case ingested before the start is never recorded.
        self.pending: OrderedDict[str, tuple[float, trace.SpanContext]] = OrderedDict()  # public id -> (ingest time, ingest span context)
        self.last_read: float | None = None  # the time of the latest queue read
        self.lock = threading.Lock()
        self.stop: Callable[[], None] = _nothing


_runtime: _Runtime | None = None  # the one live set-up, or None (off)


def is_on() -> bool:
    return _runtime is not None


def _event(record: logging.LogRecord) -> str:
    return record.msg if isinstance(record.msg, str) else type(record.msg).__name__


class JsonFormatter(logging.Formatter):
    """One JSON object per record with the contract's fields and no others (section 4)."""

    def format(self, record: logging.LogRecord) -> str:
        fields = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname if record.levelname in ("DEBUG", "INFO", "WARNING") else "ERROR",
            "logger": record.name,
            "event": _event(record),
        }
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            fields["trace_id"] = trace.format_trace_id(context.trace_id)
            fields["span_id"] = trace.format_span_id(context.span_id)
        fields["service.name"] = SERVICE_NAME
        return json.dumps(fields, separators=(",", ":"))


class _OtlpLogHandler(LoggingHandler):
    """The SDK's handler, fed a clean copy of the record: no arguments, no extra fields, exception type only."""

    def emit(self, record: logging.LogRecord) -> None:
        exc_type = record.exc_info[0] if record.exc_info else None
        clean = logging.LogRecord(
            record.name, record.levelno, record.pathname, record.lineno, _event(record), None,
            (exc_type, None, None) if exc_type else None, record.funcName,
        )  # fmt: skip
        clean.created = record.created
        super().emit(clean)


def _operator_attributes(raw: str) -> dict[str, str]:
    """The two operator-set resource attributes found in `OTEL_RESOURCE_ATTRIBUTES` (`key=value,...`).

    A value (after percent-decoding) outside `OPERATOR_VALUE` is left out, like a missing one.
    """
    found: dict[str, str] = {}
    for part in raw.split(","):
        key, separator, value = part.partition("=")
        if separator and key.strip() in OPERATOR_ATTRIBUTES and OPERATOR_VALUE.fullmatch(decoded := unquote(value.strip())):
            found[key.strip()] = decoded
    return found


def setup(
    environ: Mapping[str, str] | None = None,
    *,
    span_exporter=None,
    metric_exporter=None,
    log_exporter=None,
    stream: IO[str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], None]:
    """Set up telemetry from `environ` (default: the process environment); return the shutdown function.

    A second call shuts the first set-up down first. The returned function flushes and shuts down the
    providers and removes the handlers; it does nothing if a later `setup` already replaced its set-up.
    """
    global _runtime
    if _runtime is not None:
        _runtime.stop()
    source = os.environ if environ is None else environ
    endpoint = (source.get(ENDPOINT_VARIABLE) or "").strip().rstrip("/")
    if not endpoint:
        return _nothing

    attributes = _operator_attributes(source.get(RESOURCE_VARIABLE) or "")
    resource = Resource({"service.name": SERVICE_NAME, **attributes})

    if span_exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        span_exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=EXPORT_TIMEOUT_SECONDS)
    if metric_exporter is None:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        metric_exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", timeout=EXPORT_TIMEOUT_SECONDS)
    if log_exporter is None:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter

        log_exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs", timeout=EXPORT_TIMEOUT_SECONDS)

    tracer_provider = TracerProvider(resource=resource, sampler=ALWAYS_ON, shutdown_on_exit=False)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(metric_exporter)],
        views=[View(instrument_name=TIME_TO_QUEUE_HISTOGRAM, aggregation=ExplicitBucketHistogramAggregation(BUCKET_BOUNDS))],
        shutdown_on_exit=False,
    )
    logger_provider = LoggerProvider(resource=resource, shutdown_on_exit=False)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

    meter = meter_provider.get_meter(SERVICE_NAME)
    runtime = _Runtime(
        tracer_provider.get_tracer(SERVICE_NAME),
        meter.create_counter(INGEST_COUNTER, unit="{alert}", description="Ingest requests by outcome."),
        meter.create_histogram(TIME_TO_QUEUE_HISTOGRAM, unit="s", description="Seconds from an accepted ingest to the queue."),
        clock,
    )

    json_handler = logging.StreamHandler(sys.stdout if stream is None else stream)
    json_handler.setFormatter(JsonFormatter())
    with warnings.catch_warnings():  # the SDK's handler is marked deprecated; the replacement is not a dependency
        warnings.simplefilter("ignore", DeprecationWarning)
        otlp_handler = _OtlpLogHandler(logger_provider=logger_provider)
    app_logger = logging.getLogger("socboard")
    previous_level = app_logger.level
    app_logger.setLevel(logging.INFO)  # the root's default (WARNING) would hide the INFO events
    app_logger.addHandler(json_handler)
    app_logger.addHandler(otlp_handler)

    def stop() -> None:
        global _runtime
        if runtime.stop is _nothing:  # already stopped
            return
        runtime.stop = _nothing
        if _runtime is runtime:
            _runtime = None
        app_logger.removeHandler(json_handler)
        app_logger.removeHandler(otlp_handler)
        app_logger.setLevel(previous_level)
        for shut_down in (tracer_provider.shutdown, meter_provider.shutdown, logger_provider.shutdown):
            try:
                shut_down()
            except Exception:  # a failed flush must not break the app's own shutdown
                _log.warning("Telemetry shutdown step failed")

    runtime.stop = stop
    _runtime = runtime
    missing = [name for name in OPERATOR_ATTRIBUTES if name not in attributes]
    if missing:
        _log.warning(f"{RESOURCE_VARIABLE} does not set: {', '.join(missing)}")
    return stop


def _checked(attributes: Mapping[str, str] | None) -> dict[str, str]:
    checked = dict(attributes or {})
    if not set(checked) <= {SEVERITY_ATTRIBUTE, CASE_ID_ATTRIBUTE}:
        raise ValueError("span attribute outside the contract")
    if SEVERITY_ATTRIBUTE in checked and checked[SEVERITY_ATTRIBUTE] not in SEVERITIES:
        raise ValueError("severity outside the contract")
    if CASE_ID_ATTRIBUTE in checked and not isinstance(checked[CASE_ID_ATTRIBUTE], str):
        raise ValueError("case id must be a string")
    return checked


def annotate(span: trace.Span | None, attributes: Mapping[str, str]) -> None:
    """Add the contract's attributes to a span from `start_span`, known only later; nothing when off."""
    checked = _checked(attributes)
    if span is not None:
        span.set_attributes(checked)


@contextlib.contextmanager
def start_span(
    name: str,
    *,
    headers: Mapping[str, str] | None = None,
    links: Sequence[trace.SpanContext] = (),
    attributes: Mapping[str, str] | None = None,
) -> Iterator[trace.Span | None]:
    """Start a span of the contract's table as the current one; yields None when telemetry is off.

    `headers` (the request's, for the server span) give the parent through `traceparent`. Only the
    contract's span names and its two attributes are accepted: a name outside the table raises
    `KeyError`, an attribute or value outside the contract raises `ValueError`. No exception
    or status text is recorded on the span: an exception text could hold request text.
    """
    kind, own_trace = SPANS[name]  # KeyError for a name outside the table
    checked = _checked(attributes)
    runtime = _runtime
    if runtime is None:
        yield None
        return
    context = Context() if own_trace else (_propagator.extract(headers) if headers is not None else None)
    with runtime.tracer.start_as_current_span(
        name,
        context=context,
        kind=kind,
        attributes=checked,
        links=[trace.Link(link) for link in links],
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        yield span


def count_ingest(outcome: str) -> None:
    """Add one to `socboard.ingest.alerts` for `accepted` or `refused`; nothing when off."""
    if outcome not in OUTCOMES:
        raise ValueError("outcome outside the contract")
    if _runtime is not None:
        _runtime.counter.add(1, {"outcome": outcome})


def record_time_to_queue(seconds: float) -> None:
    """Record `socboard.alert.time_to_queue`; inside a sampled span it carries that trace's exemplar."""
    if _runtime is not None:
        _runtime.histogram.record(seconds)


def remember_ingest(case_id: str, span: trace.Span | None) -> None:
    """Keep an accepted case's public id, the time and the `ingest.alert` span context until its first queue read (#291).

    Nothing is kept when off. Holds those three things only, in a map of at most `PENDING_MAX`
    entries (the oldest dropped first) and none older than `PENDING_MAX_AGE_SECONDS`.
    """
    runtime = _runtime
    if runtime is None or span is None:
        return
    with runtime.lock:
        now = runtime.clock()
        _drop_old(runtime, now)
        while len(runtime.pending) >= PENDING_MAX:
            runtime.pending.popitem(last=False)
        runtime.pending[case_id] = (now, span.get_span_context())


def _drop_old(runtime: _Runtime, now: float) -> None:
    pending = runtime.pending  # in ingest order, so the old ones are first
    while pending and now - next(iter(pending.values()))[0] > PENDING_MAX_AGE_SECONDS:
        pending.popitem(last=False)


def queue_read(cases: Iterable[CaseLike]) -> None:
    """A `GET /api/queue` returned `cases` (each with `.id` and `.severity`): note the first sight of each remembered one (#291).

    For each case still in the map: span `queue.first_seen` linked to the ingest span, the event
    `alert.first_in_queue` under the ingest trace, and the entry removed. The histogram is recorded,
    with the ingest context (so its exemplar is the ingest's trace id), only when the queue was
    watched: the read before this one was at most `WATCH_SECONDS` before the ingest, or after it.
    A second read finds no entry and records nothing. `cases` is not read when off.
    """
    runtime = _runtime
    if runtime is None:
        return
    with runtime.lock:
        now = runtime.clock()
        _drop_old(runtime, now)
        previous, runtime.last_read = runtime.last_read, now
        found = []
        for case in cases:
            entry = runtime.pending.pop(case.id, None)
            if entry is not None:
                found.append((case, *entry))
    for case, ingested, context in found:
        watched = previous is not None and ingested - previous <= WATCH_SECONDS  # a read after the ingest makes it negative
        ingest = trace.set_span_in_context(trace.NonRecordingSpan(context))
        with start_span(
            "queue.first_seen",
            links=[context],
            attributes={SEVERITY_ATTRIBUTE: case.severity, CASE_ID_ATTRIBUTE: case.id},
        ):
            if watched:
                runtime.histogram.record(now - ingested, context=ingest)
        token = otel_context.attach(ingest)  # the log line carries the ingest's trace id, not the read's
        try:
            log_event("alert.first_in_queue")
        finally:
            otel_context.detach(token)


def log_event(event: str) -> None:
    """Log one of the contract's events at INFO on the `socboard.events` logger; nothing when off."""
    if event not in EVENTS:
        raise ValueError("event outside the contract")
    if _runtime is not None:
        logging.getLogger(EVENT_LOGGER).info(event)


class IngestSpanMiddleware:
    """Pure ASGI middleware: the server span of `POST /api/alerts`, and the count of refused ingests (#290).

    Any other request, and every `lifespan` and `websocket` scope, goes straight to the app, as does
    every request while telemetry is off. For the one route it reads `traceparent` with the SDK's
    propagator (and no other header), names the span from the route template Starlette leaves in
    `scope["route"]` once routing has run, reads the status by wrapping `send`, and records an
    exception's type only before it re-raises. A 401, 422 or 429 counts as `refused` and logs one
    fixed event; the accepted count and the `ingest.alert` span are in the route.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != INGEST_PATH or _runtime is None:
            await self.app(scope, receive, send)
            return
        carrier = {name.decode("latin-1"): value.decode("latin-1") for name, value in scope["headers"] if name in _TRACE_HEADERS}
        status = 0

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        with start_span(SERVER_SPAN, headers=carrier) as started:
            span = started or trace.INVALID_SPAN  # None only if telemetry was shut down since the check above
            try:
                await self.app(scope, receive, send_with_status)
            except Exception as error:
                span.add_event("exception", {"exception.type": type(error).__name__})
                span.set_status(StatusCode.ERROR)
                raise
            finally:
                route = scope.get("route")
                if route is not None:
                    span.update_name(f"POST {route.path}")
            if status in REFUSED_STATUSES:
                count_ingest("refused")
                log_event("alert.refused")
