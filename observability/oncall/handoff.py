"""Alert-to-agent hand-off (#297): wait for QueueLatencyOverSLO, then write the agent's input file.

    python observability/oncall/handoff.py http://127.0.0.1:9093 --grafana-url http://localhost:3000 \\
        --environment dev --version 1.2.3

Polls Alertmanager's GET /api/v2/alerts every 5 seconds until the alert is active or the timeout
ends. On a match it writes `incident.json` (path: --out) holding only checked values: a field that
fails its check is left out and its name is listed under `refused`. Every value is a fixed text, a
time parsed into one form, or a value the caller gave (--environment, --version; the payload's own
environment and version are kept only when equal to them). No payload free text enters the file, and
an extra label or annotation is listed by a fixed word, never by its name.

Exit codes: 0 the file was written; 2 a refused address or Grafana URL, an unreachable Alertmanager
or an unwritable file; 3 the timeout ended with no matching alert (nothing is written).

`parse` takes the payload of either source (the API's list or a webhook's body), so a later webhook
receiver (#301) reuses it. Standard library only.
"""

import argparse
import contextlib
import http.client
import json
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

ALERT_NAME = "QueueLatencyOverSLO"
POLL_SECONDS = 5
DEFAULT_TIMEOUT = 600
REQUEST_TIMEOUT = 5
MAX_BODY = 1024 * 1024
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
STACK_NAME = "alertmanager"  # the service name of the Alertmanager that #292 adds to the stack

# One copy of the two fixed texts of observability/alerts/queue_latency.yml; tests/test_oncall_handoff.py
# fails when either differs from the rule file (this script is standard library only, so it cannot read the YAML).
SUMMARY = "A new alert took more than 5 seconds to show in the SOCBoard queue (95th percentile of the probe over 5 minutes)."
OWNER = "socboard on-call (the maintainer)"
DASHBOARD_PATH = "/d/socboard-queue-latency"  # the path of the rule's dashboard_url; its host part is --grafana-url
# The rule's expression without its `> 5` comparison; a test finds it in the rule file.
QUERY = "histogram_quantile(0.95, sum by (le) (rate(socboard_probe_time_to_queue_seconds_bucket[5m])))"

EXPECTED_LABELS = ("alertname", "severity", "service")  # CONTRACT.md section 6
EXPECTED_ANNOTATIONS = ("summary", "environment", "version", "owner", "dashboard_url")
FIELD_VALUE = re.compile(r"[A-Za-z0-9._+-]{1,64}")  # #312's rule for environment and version
URL_TEXT = re.compile(r"[\x21-\x7e]{1,512}")
RFC3339 = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})")


class Refused(ValueError):
    """An argument that is refused before any request."""


class Unreachable(Exception):
    """Alertmanager did not answer with a usable list."""


def parse_address(url: str) -> tuple[str, int]:
    """(host, port) of an Alertmanager we may call: plain http, loopback or the stack's name, a port, no path."""
    if not url.isascii() or not url.isprintable() or " " in url or any(c in url for c in "?#@\\"):
        raise Refused("the address has a character that is not allowed")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise Refused("the address is not a valid URL") from None
    if parts.scheme != "http":
        raise Refused("the address must be plain http")
    if host not in (*LOOPBACK_HOSTS, STACK_NAME):
        raise Refused("the address must be a loopback address or " + STACK_NAME)
    if not port:
        raise Refused("the address needs a port")
    if parts.path:
        raise Refused("the address must have no path")
    return host, port


def parse_grafana_url(url: str) -> str:
    """The Grafana base URL without a trailing slash, or Refused."""
    base = url.rstrip("/")
    if not URL_TEXT.fullmatch(base) or not re.fullmatch(r"https?://[^/?#@]+", base):
        raise Refused("--grafana-url must be http or https, a host and no path")
    return base


def _time(value) -> str | None:
    """`value` as RFC 3339 written in one fixed form (UTC, whole seconds), or None."""
    match = RFC3339.fullmatch(value) if isinstance(value, str) else None
    if not match:
        return None
    try:
        moment = datetime.fromisoformat(match[1] + ("+00:00" if match[2] == "Z" else match[2]))
        moment = moment.astimezone(UTC)  # OverflowError when the UTC time is before year 1 or after 9999
    except (ValueError, OverflowError):
        return None
    # The year is padded by hand: strftime("%Y") writes "1" for year 1 on some platforms.
    return f"{moment.year:04d}" + moment.strftime("-%m-%dT%H:%M:%SZ")


def _is_active(alert: dict) -> bool:
    status = alert.get("status")
    if isinstance(status, dict):  # API v2: status.state
        return status.get("state") == "active"
    return status == "firing"  # webhook: alerts[].status


def parse(payload, grafana_url: str, environment: str, version: str) -> dict | None:
    """The incident for the first active QueueLatencyOverSLO in `payload`, or None.

    `payload` is the API v2 list or a webhook body ({"status", "alerts": [...]}); `grafana_url` is
    a checked base URL (parse_grafana_url); `environment` and `version` are the values the caller
    expects (checked by parse_field). The payload's environment and version are accepted only when
    they equal these, so every value in the result is a fixed text, a parsed time or a caller value.
    """
    if isinstance(payload, dict) and payload.get("status") == "firing":
        payload = payload.get("alerts")
    if not isinstance(payload, list):
        return None
    for alert in payload:
        if not isinstance(alert, dict) or not _is_active(alert):
            continue
        labels = alert.get("labels")
        if not isinstance(labels, dict) or labels.get("alertname") != ALERT_NAME:
            continue
        annotations = alert.get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}
        incident = {"alertname": ALERT_NAME}
        refused = []
        started = _time(alert.get("startsAt"))
        checks = {
            "startsAt": started,
            "environment": _same(annotations.get("environment"), environment),
            "version": _same(annotations.get("version"), version),
            "dashboard_url": _dashboard(annotations.get("dashboard_url"), grafana_url),
            "summary": SUMMARY if annotations.get("summary") == SUMMARY else None,
            "owner": OWNER if annotations.get("owner") == OWNER else None,
        }
        for name, value in checks.items():
            if value is None:
                refused.append(name)
            else:
                incident[name] = value
        # An extra name is payload text, so it is reported by a fixed word.
        if any(name not in EXPECTED_LABELS for name in labels):
            refused.append("extra_label")
        if any(name not in EXPECTED_ANNOTATIONS for name in annotations):
            refused.append("extra_annotation")
        incident["query"] = QUERY
        incident["refused"] = refused
        return incident
    return None


def parse_field(value: str, name: str) -> str:
    """An --environment or --version argument under #312's rule, or Refused."""
    if not FIELD_VALUE.fullmatch(value):
        raise Refused(f"{name} must be 1 to 64 characters from A-Z a-z 0-9 . _ + -")
    return value


def _same(value, expected: str) -> str | None:
    # A canary or an instruction can match #312's character rule, so a format check is not enough:
    # the payload's value is accepted only when it equals what the caller expects.
    return expected if isinstance(value, str) and value == expected else None


def _dashboard(value, base: str) -> str | None:
    # Stricter than "starts with the base": the path is the rule's fixed one, so the URL carries no payload text.
    return value if isinstance(value, str) and value == base + DASHBOARD_PATH else None


def fetch(host: str, port: int):
    """The decoded JSON of GET /api/v2/alerts. No redirect is followed, so the address rule holds."""
    connection = http.client.HTTPConnection(host, port, timeout=REQUEST_TIMEOUT)
    try:
        connection.request("GET", "/api/v2/alerts")
        response = connection.getresponse()
        body = response.read(MAX_BODY + 1)
        if response.status != 200 or len(body) > MAX_BODY:
            raise Unreachable
        return json.loads(body)
    except (OSError, http.client.HTTPException, ValueError, RecursionError):  # RecursionError: a body nested too deep
        raise Unreachable from None
    finally:
        connection.close()


def write_incident(incident: dict, path: str) -> None:
    """Write the file whole or not at all: a temp file in the same folder, then a rename."""
    handle, temp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".incident-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(incident, out, indent=2, sort_keys=True)
            out.write("\n")
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)
        raise


def main(argv=None, *, fetch_alerts=fetch, clock=time.monotonic, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("address", help="http://127.0.0.1:<port>, or http://alertmanager:<port> inside the stack network")
    parser.add_argument("--grafana-url", required=True, help="Grafana base URL; the alert's dashboard_url must start with it")
    parser.add_argument("--environment", required=True, help="the environment the alert must name (for example dev)")
    parser.add_argument("--version", required=True, help="the version the alert must name")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds to wait (default 600)")
    parser.add_argument("--out", default="incident.json", help="where to write the file (default incident.json)")
    args = parser.parse_args(argv)
    try:
        host, port = parse_address(args.address)
        grafana_url = parse_grafana_url(args.grafana_url)
        environment = parse_field(args.environment, "--environment")
        version = parse_field(args.version, "--version")
    except Refused as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.timeout < 0:
        print("refused: --timeout must not be negative", file=sys.stderr)
        return 2
    deadline = clock() + args.timeout
    while True:
        try:
            incident = parse(fetch_alerts(host, port), grafana_url, environment, version)
        except Unreachable:
            print("Alertmanager is unreachable", file=sys.stderr)
            return 2
        if incident:
            try:
                write_incident(incident, args.out)
            except OSError:
                print("cannot write the output file", file=sys.stderr)
                return 2
            print(f"wrote {args.out}")
            return 0
        remaining = deadline - clock()
        if remaining <= 0:
            print("timeout: no active " + ALERT_NAME, file=sys.stderr)
            return 3
        sleep(min(POLL_SECONDS, remaining))


if __name__ == "__main__":
    sys.exit(main())
