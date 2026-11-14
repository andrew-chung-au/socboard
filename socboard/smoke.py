"""Post-deploy smoke check (#191): `python -m socboard.smoke [--health-only] <url>`.

Five steps against a running app, each named in the output:
  1 health   GET /api/health answers 200
  2 login    POST /api/login as `lead` answers 200 with a token
  3 ingest   POST /api/alerts with the ingest key answers 201 (one synthetic alert)
  4 queue    the new case is in GET /api/queue within 5 seconds of the ingest response
  5 audit    the Lead can read the case's audit trail and it holds the `ingested` entry

`--health-only` runs step 1 and needs no credential. Each full run adds one New case to the queue
and deletes nothing. Exit 0 when every step passed, 1 when a step failed (the output names it), 2
when the arguments, the URL or a credential are refused (before any request).

The password and the key come from `SOCBOARD_SEED_PASSWORD_LEAD` and `SOCBOARD_INGEST_API_KEY`
only. Nothing is printed from a response body except a refusal's `code`, and only when it is a
short lower-case word with underscores (the text is untrusted server output). No redirect is
followed, so the password, the key and the token only ever go to the origin that was given.
Standard library only, apart from the two length constants it reads from the app's own modules.
"""

import argparse
import http.client
import json
import os
import re
import socket
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

PASSWORD_VARIABLE = "SOCBOARD_SEED_PASSWORD_LEAD"
KEY_VARIABLE = "SOCBOARD_INGEST_API_KEY"
LEAD_USERNAME = "lead"

REQUEST_TIMEOUT = 10.0  # seconds for one request
TOTAL_TIMEOUT = 60.0  # seconds for the whole run
QUEUE_WAIT = 5.0  # the spec's target: a new alert in the queue within 5 seconds
POLL_INTERVAL = 0.5
MAX_RESPONSE_BYTES = 1024 * 1024

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
CODE_PATTERN = re.compile(r"[a-z_]{1,40}")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/=-]{1,4096}")

# The six fields `IngestRequest` requires; the timestamp is added per run.
SYNTHETIC_ALERT = {
    "source": "smoke-check",
    "type": "failed_login",
    "asset": "smoke-check-host",
    "title": "Smoke check: synthetic alert",
    "description": "Synthetic alert from the post-deploy smoke check; it can be ignored.",
}


class Refused(Exception):
    """The arguments, the URL or a credential are refused: exit 2, before any request."""


class StepFailed(Exception):
    """One step failed: exit 1. The text is fixed or built from a status and a checked code."""


@dataclass(frozen=True)
class Answer:
    status: int
    body: bytes


def _error_text(answer: Answer) -> str:
    """`HTTP <status>`, plus ` code <code>` when the body holds a code of the allowed form."""
    text = f"HTTP {answer.status}"
    try:
        code = json.loads(answer.body).get("code")
    except (ValueError, AttributeError, RecursionError):
        return text
    if isinstance(code, str) and CODE_PATTERN.fullmatch(code):
        text += f" code {code}"
    return text


def parse_base_url(url: str) -> tuple[str, str, int | None, str]:
    """(scheme, host, port, path prefix) for a URL this check may send credentials to, or Refused."""
    if not url or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in url):
        raise Refused("the URL is empty or holds a space or control character")
    if "?" in url or "#" in url:
        raise Refused("the URL must have no query and no fragment")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise Refused("the URL is not valid (check the port and the brackets of an IPv6 address)") from None
    if parts.scheme not in ("http", "https"):
        raise Refused("the URL scheme must be http or https")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise Refused("the URL must not hold a user name or password")
    host = parts.hostname
    if not host:
        raise Refused("the URL has no host")
    if parts.scheme == "http" and host not in LOOPBACK_HOSTS:
        raise Refused("http is allowed for localhost, 127.0.0.1 and ::1 only; use https for any other host")
    return parts.scheme, host, port, parts.path.rstrip("/")


def read_credentials(environ: Mapping[str, str]) -> tuple[str, str]:
    """(password, key), or Refused naming each variable that is missing or too short, never a value."""
    # Read here, not at import, so `--health-only` loads nothing but the standard library.
    from socboard.ingest import MIN_KEY_LENGTH
    from socboard.seed import MIN_PASSWORD_LENGTH

    password = environ.get(PASSWORD_VARIABLE, "")
    key = environ.get(KEY_VARIABLE, "")
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"{PASSWORD_VARIABLE} must be set to at least {MIN_PASSWORD_LENGTH} characters")
    if len(key) < MIN_KEY_LENGTH:
        problems.append(f"{KEY_VARIABLE} must be set to at least {MIN_KEY_LENGTH} characters")
    if problems:
        raise Refused("; ".join(problems))
    return password, key


class Client:
    """One request at a time to one origin. No redirect is followed, no body is printed."""

    def __init__(self, scheme: str, host: str, port: int | None, prefix: str, deadline: float):
        self.scheme, self.host, self.port, self.prefix = scheme, host, port, prefix
        self.deadline = deadline

    def request(
        self, method: str, path: str, *, headers: Mapping[str, str] | None = None, body: object = None, timeout_cap: float | None = None
    ) -> Answer:
        """The answer, or StepFailed. The request stops at the single-request limit, at `timeout_cap`
        and at the total deadline, whichever is first."""
        remaining = self.deadline - time.monotonic()
        limit = min(REQUEST_TIMEOUT, remaining, timeout_cap if timeout_cap is not None else REQUEST_TIMEOUT)
        if limit <= 0:
            raise StepFailed("timed out (the total time limit was reached)")
        request_end = time.monotonic() + limit
        connection_class = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        # An IPv6 address needs its brackets here, or http.client reads "::1" as a host and a port.
        host = f"[{self.host}]" if ":" in self.host else self.host
        connection = connection_class(host, self.port, timeout=limit)
        expired = threading.Event()

        held: list[socket.socket] = []

        def expire() -> None:
            """The whole request is out of time: shut the socket so a blocked read ends. The socket
            timeout alone restarts on every byte, so a server sending one byte at a time would
            otherwise hold the request open. `http.client` drops `connection.sock` when the answer
            will close the connection, but the response keeps reading from it, so the socket is
            also held here."""
            expired.set()
            sock = connection.sock or (held[0] if held else None)
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        watchdog = threading.Timer(limit, expire)
        watchdog.daemon = True
        watchdog.start()
        send_headers = {"Accept": "application/json", "Connection": "close", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            send_headers["Content-Type"] = "application/json"
        try:
            connection.request(method, self.prefix + path, body=data, headers=send_headers)
            if connection.sock is not None:
                held.append(connection.sock)
            response = connection.getresponse()
            chunks = []
            size = 0
            while True:
                if time.monotonic() > request_end:
                    raise StepFailed("timed out")
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise StepFailed("the response is too large")
                chunks.append(chunk)
            if expired.is_set():  # a body that ends at the shut socket is not a whole answer
                raise StepFailed("timed out")
            return Answer(response.status, b"".join(chunks))
        except StepFailed:
            raise
        except TimeoutError:
            raise StepFailed("timed out") from None
        except (OSError, http.client.HTTPException, ValueError) as error:
            if expired.is_set():
                raise StepFailed("timed out") from None
            # The class name only: the message may hold a header value.
            raise StepFailed(f"no usable answer ({type(error).__name__})") from None
        finally:
            watchdog.cancel()
            connection.close()


def _json(answer: Answer) -> object:
    try:
        return json.loads(answer.body)
    except (ValueError, RecursionError):
        raise StepFailed(f"HTTP {answer.status} with a body that is not JSON") from None


def step_health(client: Client) -> None:
    answer = client.request("GET", "/api/health")
    if answer.status != 200:
        raise StepFailed(_error_text(answer))


def step_login(client: Client, password: str) -> str:
    answer = client.request("POST", "/api/login", body={"username": LEAD_USERNAME, "password": password})
    if answer.status != 200:
        raise StepFailed(_error_text(answer))
    parsed = _json(answer)
    token = parsed.get("token") if isinstance(parsed, dict) else None
    if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
        raise StepFailed("HTTP 200 without a usable token")
    return token


def step_ingest(client: Client, key: str) -> str:
    alert = {**SYNTHETIC_ALERT, "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
    answer = client.request("POST", "/api/alerts", headers={"X-API-Key": key}, body=alert)
    if answer.status != 201:
        raise StepFailed(_error_text(answer))
    parsed = _json(answer)
    case_id = parsed.get("id") if isinstance(parsed, dict) else None
    if not isinstance(case_id, str) or not case_id:
        raise StepFailed("HTTP 201 without a case id")
    return case_id


def step_queue(client: Client, token: str, case_id: str, started: float) -> None:
    """Poll the queue until it holds `case_id`, for `QUEUE_WAIT` seconds from `started`."""
    end = started + QUEUE_WAIT
    while True:
        left = end - time.monotonic()
        if left <= 0:
            raise StepFailed(f"the case was not in the queue within {QUEUE_WAIT:g} seconds")
        answer = client.request("GET", "/api/queue", headers={"Authorization": f"Bearer {token}"}, timeout_cap=left)
        if answer.status != 200:
            raise StepFailed(_error_text(answer))
        parsed = _json(answer)
        cases = parsed.get("cases") if isinstance(parsed, dict) else None
        if isinstance(cases, list) and any(isinstance(case, dict) and case.get("id") == case_id for case in cases):
            return
        time.sleep(max(0.0, min(POLL_INTERVAL, end - time.monotonic())))


def step_audit(client: Client, token: str, case_id: str) -> None:
    answer = client.request(
        "GET", f"/api/cases/{quote(case_id, safe='')}/audit", headers={"Authorization": f"Bearer {token}"}
    )
    if answer.status != 200:
        raise StepFailed(_error_text(answer))
    entries = _json(answer)
    if not isinstance(entries, list) or not any(isinstance(entry, dict) and entry.get("action") == "ingested" for entry in entries):
        raise StepFailed("the audit trail has no ingested entry")


def run_steps(client: Client, health_only: bool, password: str, key: str, say: Callable[[str], None]) -> int:
    """Run the steps in order; print one line per step; the exit code (0 or 1)."""
    state: dict[str, Any] = {}

    def run(number: int, name: str, action: Callable[[], None]) -> bool:
        try:
            action()
        except StepFailed as failure:
            say(f"step {number} {name}: FAILED ({failure})")
            return False
        except Exception as error:  # a shape no step expected: a step line, never a traceback or a value
            say(f"step {number} {name}: FAILED (unexpected {type(error).__name__})")
            return False
        say(f"step {number} {name}: ok")
        return True

    def login() -> None:
        state["token"] = step_login(client, password)

    def ingest() -> None:
        state["case"] = step_ingest(client, key)
        state["ingested_at"] = time.monotonic()

    steps: list[tuple[int, str, Callable[[], None]]] = [(1, "health", lambda: step_health(client))]
    if not health_only:
        steps += [
            (2, "login", login),
            (3, "ingest", ingest),
            (4, "queue", lambda: step_queue(client, state["token"], state["case"], state["ingested_at"])),
            (5, "audit", lambda: step_audit(client, state["token"], state["case"])),
        ]
    for number, name, action in steps:
        if not run(number, name, action):
            return 1
    say("smoke check: passed" + (" (health only)" if health_only else ""))
    return 0


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m socboard.smoke", description="Check a running SOCBoard.")
    parser.add_argument("url", help="http://127.0.0.1:<port> on the server, or the public https URL with --health-only")
    parser.add_argument("--health-only", action="store_true", help="check the health route only; needs no credential")
    args = parser.parse_args(argv)  # a bad argument exits 2
    source = os.environ if environ is None else environ
    try:
        scheme, host, port, prefix = parse_base_url(args.url)
        password, key = ("", "") if args.health_only else read_credentials(source)
    except Refused as refusal:
        print(f"smoke check: refused: {refusal}", file=sys.stderr)
        return 2
    client = Client(scheme, host, port, prefix, deadline=time.monotonic() + TOTAL_TIMEOUT)
    return run_steps(client, args.health_only, password, key, lambda line: print(line, flush=True))


if __name__ == "__main__":
    raise SystemExit(main())
