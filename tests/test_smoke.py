"""The post-deploy smoke check (#191): `socboard/smoke.py`, run against stub servers.

Each stub is the standard library's `http.server` on 127.0.0.1 with a free port, so no real app,
network or Docker is needed. Every value here is synthetic.
"""

import ast
import http.client
import json
import re
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from socboard import smoke

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Zq7-synthetic-lead-secret"
KEY = "synthetic-ingest-key-0123456789"
TOKEN = "synthetic.token-value_abc123"
CASE_ID = "41"
ENVIRON = {smoke.PASSWORD_VARIABLE: PASSWORD, smoke.KEY_VARIABLE: KEY}
BODY_MARKER = "BODYMARKER-must-never-be-printed"
SECRETS = (PASSWORD, KEY, TOKEN, BODY_MARKER)


# --- the stub server ----------------------------------------------------------------------------


def reply(status, body=None, headers=None):
    return status, body, headers or {}


def good_routes(queue_after=1, state=None):
    """The five steps answered as the real app answers them. `state` counts queue polls."""
    state = state if state is not None else {"queue_polls": 0}

    def login(request):
        body = json.loads(request.body)
        if body == {"username": "lead", "password": PASSWORD}:
            return reply(200, {"token": TOKEN, "user": {"id": "3", "username": "lead", "role": "lead"}})
        return reply(401, {"code": "invalid_credentials", "message": "no"})

    def ingest(request):
        if request.headers.get("X-API-Key") != KEY:
            return reply(401, {"code": "unauthorized", "message": "no"})
        return reply(201, {"id": CASE_ID, "title": json.loads(request.body)["title"]})

    def queue(request):
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return reply(401, {"code": "unauthorized", "message": "no"})
        state["queue_polls"] += 1
        cases = [{"id": "7"}] + ([{"id": CASE_ID}] if state["queue_polls"] >= queue_after else [])
        return reply(200, {"cases": cases, "servedAt": "2026-11-14T12:00:00Z"})

    def audit(request):
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return reply(401, {"code": "unauthorized", "message": "no"})
        return reply(200, [{"id": "1", "action": "ingested", "actor": "system", "detail": "x", "at": "2026-11-14T12:00:00Z"}])

    return {
        ("GET", "/api/health"): lambda request: reply(200, {"status": "ok"}),
        ("POST", "/api/login"): login,
        ("POST", "/api/alerts"): ingest,
        ("GET", "/api/queue"): queue,
        ("GET", f"/api/cases/{CASE_ID}/audit"): audit,
    }


class Recorded:
    def __init__(self, method, path, headers, body):
        self.method, self.path, self.headers, self.body = method, path, headers, body


class Raw:
    """A reply the stub writes byte for byte (a status line, headers and a body of its own), so a
    test can send a length it does not deliver."""

    def __init__(self, data):
        self.bytes = data


class Stub:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def handle_any(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8") if length else ""
                request = Recorded(self.command, self.path, self.headers, body)
                stub.requests.append(request)
                route = stub.routes.get((self.command, self.path.split("?")[0]))
                status, payload, headers = route(request) if route else reply(404, {"code": "not_found"})
                if isinstance(payload, Raw):  # the exact bytes, then the connection closes
                    self.wfile.write(payload.bytes)
                    return
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = handle_any

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def paths(self):
        return [(request.method, request.path) for request in self.requests]


@pytest.fixture
def make_stub():
    stubs = []

    def make(routes=None):
        stub = Stub(routes if routes is not None else good_routes())
        stubs.append(stub)
        return stub

    yield make
    for stub in stubs:
        stub.close()


@pytest.fixture(autouse=True)
def fast_clock(monkeypatch):
    """Short waits so the failing cases are quick; the constants are read when a step runs."""
    monkeypatch.setattr(smoke, "POLL_INTERVAL", 0.02)
    monkeypatch.setattr(smoke, "QUEUE_WAIT", 1.0)
    monkeypatch.setattr(smoke, "REQUEST_TIMEOUT", 2.0)
    monkeypatch.setattr(smoke, "TOTAL_TIMEOUT", 10.0)


def run(url, capsys, *flags, environ=ENVIRON):
    code = smoke.main([*flags, url], environ)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def assert_no_secrets(output):
    for secret in SECRETS:
        assert secret not in output


# --- the five steps -----------------------------------------------------------------------------


def test_all_five_steps_pass_in_order_and_send_each_credential_only_where_it_belongs(make_stub, capsys):
    stub = make_stub()
    code, output = run(stub.url, capsys)

    assert code == 0, output
    lines = [line for line in output.splitlines() if line.startswith("step ")]
    assert [line.split(":")[0] for line in lines] == ["step 1 health", "step 2 login", "step 3 ingest", "step 4 queue", "step 5 audit"]
    assert all(line.endswith(": ok") for line in lines)
    assert_no_secrets(output)

    paths = stub.paths()
    assert paths[:3] == [("GET", "/api/health"), ("POST", "/api/login"), ("POST", "/api/alerts")]
    assert paths[3:-1] == [("GET", "/api/queue")] * (len(paths) - 4)
    assert paths[-1] == ("GET", f"/api/cases/{CASE_ID}/audit")
    by_path = {request.path: request for request in stub.requests}
    assert by_path["/api/health"].headers.get("Authorization") is None
    assert by_path["/api/health"].headers.get("X-API-Key") is None
    assert by_path["/api/login"].headers.get("X-API-Key") is None
    assert by_path["/api/alerts"].headers.get("X-API-Key") == KEY
    assert by_path["/api/alerts"].headers.get("Authorization") is None  # the token never goes to ingest
    assert by_path["/api/queue"].headers.get("X-API-Key") is None  # the key never goes to the queue
    assert by_path[f"/api/cases/{CASE_ID}/audit"].headers.get("X-API-Key") is None


def test_the_check_deletes_nothing_and_sends_get_and_post_only(make_stub, capsys):
    stub = make_stub()
    run(stub.url, capsys)
    assert {request.method for request in stub.requests} <= {"GET", "POST"}


def test_the_synthetic_alert_has_the_six_required_fields_and_says_it_is_synthetic(make_stub, capsys):
    stub = make_stub()
    before = datetime.now(UTC).replace(microsecond=0)
    run(stub.url, capsys)
    sent = json.loads(next(request.body for request in stub.requests if request.path == "/api/alerts"))
    assert set(sent) == {"source", "type", "asset", "timestamp", "title", "description"}
    assert sent["source"] == "smoke-check"
    assert sent["type"] == "failed_login"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", sent["timestamp"])
    stamp = datetime.fromisoformat(sent["timestamp"].replace("Z", "+00:00"))
    assert 0 <= (stamp - before).total_seconds() < 30
    assert "synthetic" in sent["description"].lower()


def test_the_case_is_found_by_the_id_in_the_201_response_and_the_queue_is_polled(make_stub, capsys):
    state = {"queue_polls": 0}
    stub = make_stub(good_routes(queue_after=4, state=state))
    code, output = run(stub.url, capsys)
    assert code == 0, output
    assert state["queue_polls"] == 4


def test_a_case_with_another_id_does_not_count(make_stub, capsys):
    routes = good_routes()
    routes[("GET", "/api/queue")] = lambda request: reply(200, {"cases": [{"id": "999"}], "servedAt": "x"})
    code, output = run(make_stub(routes).url, capsys)
    assert code == 1
    assert "step 4 queue: FAILED" in output
    assert "not in the queue within" in output
    assert "step 5" not in output


def test_the_queue_wait_starts_at_the_ingest_response_and_each_poll_gets_only_the_time_left(make_stub, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "QUEUE_WAIT", 0.5)
    routes = good_routes()

    def slow_queue(request):
        time.sleep(3)  # longer than the whole wait
        return reply(200, {"cases": [{"id": CASE_ID}], "servedAt": "x"})

    routes[("GET", "/api/queue")] = slow_queue
    started = time.monotonic()
    code, output = run(make_stub(routes).url, capsys)
    assert code == 1
    assert "step 4 queue: FAILED (the case was not in the queue within 0.5 seconds)" in output
    assert time.monotonic() - started < 2.5  # stopped by the wait, not by the 3 second answer


def test_health_only_runs_step_1_and_needs_no_credential(make_stub, capsys):
    stub = make_stub()
    code, output = run(stub.url, capsys, "--health-only", environ={})
    assert code == 0
    assert stub.paths() == [("GET", "/api/health")]
    assert "step 2" not in output
    assert "health only" in output


# --- every step failing -------------------------------------------------------------------------


def failing(method, path, status, payload=None, headers=None):
    routes = good_routes()
    routes[(method, path)] = lambda request: reply(status, payload, headers)
    return routes


STEP_FAILURES = [
    ("step 1 health", failing("GET", "/api/health", 503, {"code": "unavailable", "message": BODY_MARKER})),
    ("step 2 login", failing("POST", "/api/login", 500, {"message": BODY_MARKER})),
    ("step 3 ingest", failing("POST", "/api/alerts", 401, {"code": "unauthorized", "message": BODY_MARKER})),
    ("step 3 ingest", failing("POST", "/api/alerts", 201, {"title": "no id here"})),
    ("step 4 queue", failing("GET", "/api/queue", 500, {"message": BODY_MARKER})),
    ("step 4 queue", failing("GET", "/api/queue", 200, b"not json " + BODY_MARKER.encode())),
    ("step 5 audit", failing("GET", f"/api/cases/{CASE_ID}/audit", 403, {"code": "forbidden", "message": BODY_MARKER})),
    ("step 5 audit", failing("GET", f"/api/cases/{CASE_ID}/audit", 200, [{"action": "claimed"}])),
    ("step 5 audit", failing("GET", f"/api/cases/{CASE_ID}/audit", 200, {"action": "ingested"})),
]


@pytest.mark.parametrize(("step", "routes"), STEP_FAILURES, ids=[f"{step}-{index}" for index, (step, _) in enumerate(STEP_FAILURES)])
def test_each_step_failing_names_that_step_and_exits_1(step, routes, make_stub, capsys):
    code, output = run(make_stub(routes).url, capsys)
    assert code == 1
    assert f"{step}: FAILED" in output
    assert_no_secrets(output)  # the password, the key, the token and the response body


def test_a_step_stops_the_run_at_the_first_failure(make_stub, capsys):
    stub = make_stub(failing("POST", "/api/login", 401, {"code": "invalid_credentials"}))
    code, output = run(stub.url, capsys)
    assert code == 1
    assert stub.paths() == [("GET", "/api/health"), ("POST", "/api/login")]
    assert "step 3" not in output


def test_a_wrong_password_is_one_401_and_is_not_retried(make_stub, capsys):
    stub = make_stub()
    code, output = run(stub.url, capsys, environ={**ENVIRON, smoke.PASSWORD_VARIABLE: "wrong-password-123"})
    assert code == 1
    assert "step 2 login: FAILED (HTTP 401 code invalid_credentials)" in output
    assert stub.paths().count(("POST", "/api/login")) == 1
    assert "wrong-password-123" not in output


@pytest.mark.parametrize(("step", "method", "path"), [("step 2 login", "POST", "/api/login"), ("step 3 ingest", "POST", "/api/alerts")])
def test_a_429_is_reported_with_its_code_and_not_retried(step, method, path, make_stub, capsys):
    stub = make_stub(failing(method, path, 429, {"code": "rate_limited", "message": BODY_MARKER}, {"Retry-After": "30"}))
    code, output = run(stub.url, capsys)
    assert code == 1
    assert f"{step}: FAILED (HTTP 429 code rate_limited)" in output
    assert stub.paths().count((method, path)) == 1
    assert_no_secrets(output)


# --- the refusal code is untrusted output --------------------------------------------------------

CODE_CASES = [
    ("<b>x</b>", False),
    ("rate limited", False),
    ("RATE_LIMITED", False),
    ("a" * 41, False),
    ("", False),
    (None, False),
    (42, False),
    ("a", True),
    ("a" * 40, True),
    ("not_found", True),
]


@pytest.mark.parametrize(("code_value", "printed"), CODE_CASES, ids=[repr(case[0]) for case in CODE_CASES])
def test_a_code_is_printed_only_when_it_is_a_short_lower_case_word(code_value, printed, make_stub, capsys):
    stub = make_stub(failing("POST", "/api/login", 403, {"code": code_value, "message": BODY_MARKER}))
    code, output = run(stub.url, capsys)
    assert code == 1
    assert ("code " in output) == printed
    if printed:
        assert f"code {code_value})" in output
    elif isinstance(code_value, str) and code_value:
        assert code_value not in output
    assert_no_secrets(output)


# --- no redirect is followed --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "method", "path"),
    [("step 1 health", "GET", "/api/health"), ("step 2 login", "POST", "/api/login"), ("step 3 ingest", "POST", "/api/alerts")],
)
def test_a_redirect_is_not_followed_so_nothing_goes_to_another_place(step, method, path, make_stub, capsys):
    routes = failing(method, path, 302, b"", {"Location": "/elsewhere"})
    routes[("GET", "/elsewhere")] = lambda request: reply(200, {"status": "ok"})
    routes[("POST", "/elsewhere")] = lambda request: reply(200, {"status": "ok"})
    stub = make_stub(routes)
    code, output = run(stub.url, capsys)
    assert code == 1
    assert f"{step}: FAILED (HTTP 302)" in output
    assert all(request.path != "/elsewhere" for request in stub.requests)
    assert stub.paths().count((method, path)) == 1


def test_a_redirect_to_another_origin_gets_no_request_there(make_stub, capsys):
    other = make_stub({("POST", "/api/login"): lambda request: reply(200, {"token": TOKEN})})
    routes = failing("POST", "/api/login", 307, b"", {"Location": f"{other.url}/api/login"})
    code, output = run(make_stub(routes).url, capsys)
    assert code == 1
    assert other.requests == []  # the password was never sent to the other origin


# --- the URL rules ------------------------------------------------------------------------------

# --- a truncated answer is a failed step (#212) -------------------------------------------------

CUT = "the answer was cut short"
AUDIT_PATH = f"/api/cases/{CASE_ID}/audit"
# (step line, method, path, status line of the cut answer)
CUT_STEPS = [
    ("step 1 health", "GET", "/api/health", "200 OK"),
    ("step 2 login", "POST", "/api/login", "200 OK"),
    ("step 3 ingest", "POST", "/api/alerts", "201 Created"),
    ("step 4 queue", "GET", "/api/queue", "200 OK"),
    ("step 5 audit", "GET", AUDIT_PATH, "200 OK"),
]


def cut_answer(status_line, declared=500, delivered=b'{"status": "o'):
    return Raw(f"HTTP/1.0 {status_line}\r\nContent-Type: application/json\r\nContent-Length: {declared}\r\n\r\n".encode() + delivered)


def routes_with(method, path, raw):
    routes = good_routes()
    routes[(method, path)] = lambda request: reply(0, raw)
    return routes


@pytest.mark.parametrize(("step", "method", "path", "status_line"), CUT_STEPS, ids=[case[0] for case in CUT_STEPS])
def test_a_body_shorter_than_its_content_length_fails_its_step_as_cut_short(step, method, path, status_line, make_stub, capsys):
    stub = make_stub(routes_with(method, path, cut_answer(status_line)))
    code, output = run(stub.url, capsys)
    assert code == 1
    assert f"{step}: FAILED ({CUT})" in output
    assert stub.paths().count((method, path)) == 1  # a cut poll at step 4 is not polled again
    assert_no_secrets(output)
    later = [line for line in output.splitlines() if line.startswith("step ") and line < step]
    assert all(line.endswith(": ok") for line in later)


@pytest.mark.parametrize("status_line", ["401 Unauthorized", "429 Too Many Requests", "500 Internal Server Error", "403 Forbidden"])
def test_a_cut_answer_with_another_status_is_cut_short_and_prints_no_code_from_the_partial_body(status_line, make_stub, capsys):
    raw = cut_answer(status_line, declared=500, delivered=b'{"code": "rate_limited", "mess')
    code, output = run(make_stub(routes_with("POST", "/api/login", raw)).url, capsys)
    assert code == 1
    assert f"step 2 login: FAILED ({CUT})" in output
    assert "rate_limited" not in output
    assert "HTTP 4" not in output and "HTTP 5" not in output


CHUNKED = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Type: application/json\r\n\r\n"
CHUNKED_CUTS = [
    ("before the first size line", CHUNKED),
    ("inside a chunk", CHUNKED + b"10\r\nabc"),
    ("after a whole chunk with no final chunk", CHUNKED + b"4\r\nabcd\r\n"),
]


@pytest.mark.parametrize(("data",), [(case[1],) for case in CHUNKED_CUTS], ids=[case[0] for case in CHUNKED_CUTS])
def test_a_chunked_answer_that_ends_before_its_last_chunk_is_cut_short(data, make_stub, capsys):
    code, output = run(make_stub(routes_with("GET", "/api/health", Raw(data))).url, capsys)
    assert code == 1
    assert f"step 1 health: FAILED ({CUT})" in output
    assert "IncompleteRead" not in output


WHOLE = b'{"status": "ok"}'
WHOLE_ANSWERS = [
    ("no length, ends at the close (HTTP/1.0)", b"HTTP/1.0 200 OK\r\n\r\n" + WHOLE),
    ("no length, Connection close (HTTP/1.1)", b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + WHOLE),
    ("a matching Content-Length", b"HTTP/1.0 200 OK\r\nContent-Length: " + str(len(WHOLE)).encode() + b"\r\n\r\n" + WHOLE),
    ("more bytes than the Content-Length (the rest is ignored)", b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n" + WHOLE),
    ("a Content-Length that is not a number", b"HTTP/1.0 200 OK\r\nContent-Length: abc\r\n\r\n" + WHOLE),
    ("a negative Content-Length", b"HTTP/1.0 200 OK\r\nContent-Length: -5\r\n\r\n" + WHOLE),
    ("a chunked answer with its last chunk", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nabcd\r\n0\r\n\r\n"),
]


@pytest.mark.parametrize(("data",), [(case[1],) for case in WHOLE_ANSWERS], ids=[case[0] for case in WHOLE_ANSWERS])
def test_an_answer_that_is_whole_still_passes_step_1(data, make_stub, capsys):
    code, output = run(make_stub(routes_with("GET", "/api/health", Raw(data))).url, capsys, "--health-only", environ={})
    assert code == 0, output
    assert "step 1 health: ok" in output


def test_a_204_with_a_content_length_and_no_body_is_not_reported_as_cut_short(make_stub, capsys):
    raw = Raw(b"HTTP/1.0 204 No Content\r\nContent-Length: 10\r\n\r\n")
    code, output = run(make_stub(routes_with("GET", "/api/health", raw)).url, capsys, "--health-only", environ={})
    assert code == 1
    assert "step 1 health: FAILED (HTTP 204)" in output  # the status is wrong for health, the body is not cut
    assert CUT not in output


def test_the_response_too_large_text_wins_over_cut_short(make_stub, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "MAX_RESPONSE_BYTES", 100)
    raw = cut_answer("200 OK", declared=5000, delivered=b"x" * 400)
    code, output = run(make_stub(routes_with("GET", "/api/health", raw)).url, capsys, "--health-only", environ={})
    assert code == 1
    assert "step 1 health: FAILED (the response is too large)" in output


def test_a_cut_answer_that_comes_after_the_time_limit_says_timed_out(capsys, monkeypatch):
    """Time wins: a declared length sent slowly and then cut when the watchdog shuts the socket."""
    monkeypatch.setattr(smoke, "REQUEST_TIMEOUT", 0.5)
    # Headers with a declared length, a few body bytes, then silence on an open connection: the
    # read blocks until the watchdog shuts the socket, and what it then sees is a short body.
    for preamble, forever, silent in (
        (b"HTTP/1.0 200 OK\r\nContent-Length: 100000\r\n\r\nabc", False, True),
        (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10\r\nabc", False, True),
        (b"HTTP/1.0 200 OK\r\nContent-Length: 100000\r\n\r\n", True, False),
    ):
        server = Trickle(preamble, forever, slow_preamble=False, silent=silent)
        try:
            code, output = run(server.url, capsys, "--health-only", environ={})
        finally:
            server.close()
        assert code == 1
        assert "step 1 health: FAILED (timed out)" in output
        assert CUT not in output


def test_the_client_reads_the_bytes_still_owed_from_http_client(make_stub):
    """The check relies on `HTTPResponse.length` (an undocumented attribute of http.client): zero
    for a whole body, the bytes owed for a short one. This pins that on the Python in use."""
    stub = make_stub(routes_with("GET", "/api/health", cut_answer("200 OK", declared=50, delivered=b"abcd")))
    host, port = stub.url.removeprefix("http://").split(":")
    connection = http.client.HTTPConnection(host, int(port), timeout=5)
    connection.request("GET", "/api/health")
    response = connection.getresponse()
    while response.read(65536):
        pass
    assert response.length == 46
    connection.close()


# --- a URL with a character above ASCII is refused (#212) ----------------------------------------

NON_ASCII_LINE = "smoke check: refused: the URL must hold ASCII characters only"
NON_ASCII_URLS = [
    ("a path", "http://127.0.0.1:8000/caf\u00e9"),
    ("a path on https", "https://example.com/caf\u00e9"),
    ("an https host", "https://b\u00fccher.example"),
    ("a fullwidth host", "https://\uff11\uff12\uff17.0.0.1"),
    ("a non-ASCII port digit", "http://127.0.0.1:\uff18\uff10\uff18\uff10"),
    ("a non-ASCII scheme letter", "ht\u0442p://127.0.0.1:8000"),
    ("a non-ASCII user part", "https://us\u00e9r@example.com"),
    ("a lone surrogate from an undecodable byte", "http://127.0.0.1:8000/\udcff"),
    ("a loopback host spelled with a non-ASCII letter", "http://l\u00f6calhost:8000"),
]


@pytest.mark.parametrize("flags", [(), ("--health-only",)], ids=["full", "health-only"])
@pytest.mark.parametrize(("url",), [(case[1],) for case in NON_ASCII_URLS], ids=[case[0] for case in NON_ASCII_URLS])
def test_a_non_ascii_url_exits_2_with_one_fixed_line_before_any_lookup_or_request(url, flags, capsys, monkeypatch):
    lookups = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: lookups.append(args) or [])
    code, output = run(url, capsys, *flags)
    assert code == 2
    assert output.splitlines() == [NON_ASCII_LINE]
    assert output.isascii()  # no part of the URL is echoed
    assert lookups == []


def test_a_non_ascii_path_on_a_listening_server_sends_no_request(make_stub, capsys):
    stub = make_stub()
    code, output = run(stub.url + "/caf\u00e9", capsys, "--health-only", environ={})
    assert code == 2
    assert NON_ASCII_LINE in output
    assert stub.requests == []


def test_a_url_with_a_space_and_a_non_ascii_character_gets_the_space_text(capsys):
    code, output = run("http://127.0.0.1:8000/caf\u00e9 x", capsys)
    assert code == 2
    assert "space or control character" in output
    assert output.isascii()


def test_an_xn_form_host_is_still_accepted():
    assert smoke.parse_base_url("https://xn--bcher-kva.example") == ("https", "xn--bcher-kva.example", None, "")


REFUSED_URLS = [
    ("embedded credentials", "http://user:secretpw@127.0.0.1:8000"),
    ("embedded user only", "http://user@127.0.0.1:8000"),
    ("an @ in the host part", "https://good.example@evil.example"),
    ("scheme ftp", "ftp://127.0.0.1:8000"),
    ("scheme file", "file:///etc/hostname"),
    ("scheme javascript", "javascript:alert(1)"),
    ("no scheme", "127.0.0.1:8000"),
    ("missing host (http)", "http://"),
    ("missing host (https)", "https://"),
    ("missing host with a port", "https://:8000"),
    ("a query", "http://127.0.0.1:8000/?a=1"),
    ("an empty query", "http://127.0.0.1:8000?"),
    ("a fragment", "http://127.0.0.1:8000/#a"),
    ("an empty fragment", "https://example.com/#"),
    ("http to a public host", "http://example.com"),
    ("http to a private address", "http://10.0.0.5:8000"),
    ("http to a look-alike of localhost", "http://localhost.example.com"),
    ("http to 0.0.0.0", "http://0.0.0.0:8000"),
    ("a bad port", "http://127.0.0.1:99999"),
    ("a space", "http://127.0.0.1:8000/a b"),
    ("a control character", "http://127.0.0.1:8000/\r\nX-Evil: 1"),
    ("empty", ""),
]


@pytest.mark.parametrize(("url",), [(case[1],) for case in REFUSED_URLS], ids=[case[0] for case in REFUSED_URLS])
def test_a_refused_url_exits_2_before_any_request_and_is_not_echoed(url, capsys):
    code, output = run(url, capsys)
    assert code == 2
    assert "refused" in output
    assert "secretpw" not in output
    assert "evil.example" not in output
    assert_no_secrets(output)


def test_a_refused_url_sends_nothing(make_stub, capsys):
    stub = make_stub()
    code, _ = run(stub.url + "/?x=1", capsys)
    assert code == 2
    assert stub.requests == []


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8000", ("http", "127.0.0.1", 8000, "")),
        ("http://localhost:8000/", ("http", "localhost", 8000, "")),
        ("http://LOCALHOST", ("http", "localhost", None, "")),
        ("http://[::1]:8000", ("http", "::1", 8000, "")),
        ("https://example.com", ("https", "example.com", None, "")),
        ("https://example.com/", ("https", "example.com", None, "")),
        ("https://example.com:8443/app/", ("https", "example.com", 8443, "/app")),
        ("https://127.0.0.1", ("https", "127.0.0.1", None, "")),
    ],
)
def test_the_allowed_urls(url, expected):
    assert smoke.parse_base_url(url) == expected


def test_health_only_applies_the_same_url_rules(capsys):
    code, output = run("http://example.com", capsys, "--health-only", environ={})
    assert code == 2
    assert "refused" in output


# --- the credentials ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "named", "not_named"),
    [
        ({}, [smoke.PASSWORD_VARIABLE, smoke.KEY_VARIABLE], []),
        ({smoke.KEY_VARIABLE: KEY}, [smoke.PASSWORD_VARIABLE], [smoke.KEY_VARIABLE]),
        ({smoke.PASSWORD_VARIABLE: PASSWORD}, [smoke.KEY_VARIABLE], [smoke.PASSWORD_VARIABLE]),
        ({smoke.PASSWORD_VARIABLE: "short-pw1", smoke.KEY_VARIABLE: KEY}, [smoke.PASSWORD_VARIABLE], [smoke.KEY_VARIABLE]),
        ({smoke.PASSWORD_VARIABLE: PASSWORD, smoke.KEY_VARIABLE: "short-key"}, [smoke.KEY_VARIABLE], [smoke.PASSWORD_VARIABLE]),
        ({smoke.PASSWORD_VARIABLE: "", smoke.KEY_VARIABLE: ""}, [smoke.PASSWORD_VARIABLE, smoke.KEY_VARIABLE], []),
    ],
)
def test_a_missing_or_short_credential_exits_2_naming_the_variable_and_sends_nothing(environ, named, not_named, make_stub, capsys):
    stub = make_stub()
    code, output = run(stub.url, capsys, environ=environ)
    assert code == 2
    for name in named:
        assert name in output
    for name in not_named:
        assert name not in output
    for value in ("short-pw1", "short-key", PASSWORD, KEY):
        assert value not in output
    assert stub.requests == []


# --- time limits and sizes ----------------------------------------------------------------------


def never_in_time(request):
    time.sleep(1.5)
    return reply(200, {"status": "ok"})


def test_a_request_that_is_not_answered_in_time_fails_its_step(make_stub, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_TIMEOUT", 0.3)
    routes = good_routes()
    routes[("GET", "/api/health")] = never_in_time
    started = time.monotonic()
    code, output = run(make_stub(routes).url, capsys)
    assert code == 1
    assert "step 1 health: FAILED (timed out" in output
    assert time.monotonic() - started < 1.2


def test_the_whole_run_stops_at_the_total_limit(make_stub, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "TOTAL_TIMEOUT", 0.6)
    monkeypatch.setattr(smoke, "REQUEST_TIMEOUT", 5.0)
    routes = good_routes()

    def slowly(handler):
        def answer(request):
            time.sleep(0.4)
            return handler(request)

        return answer

    routes = {key: slowly(handler) for key, handler in routes.items()}
    started = time.monotonic()
    code, output = run(make_stub(routes).url, capsys)
    assert code == 1
    assert "FAILED (timed out" in output
    assert "step 5 audit: ok" not in output
    assert time.monotonic() - started < 2.0


def test_a_response_over_the_size_limit_fails_its_step(make_stub, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "MAX_RESPONSE_BYTES", 100)
    routes = failing("GET", "/api/health", 200, b"x" * 5000)
    code, output = run(make_stub(routes).url, capsys)
    assert code == 1
    assert "step 1 health: FAILED (the response is too large)" in output


class Trickle:
    """A raw server that sends its answer one byte at a time, so no single read ever times out."""

    def __init__(self, preamble, forever, slow_preamble=True, silent=False):
        self.slow_preamble, self.silent = slow_preamble, silent
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(5)
        self.preamble, self.forever = preamble, forever
        self.url = f"http://127.0.0.1:{self.server.getsockname()[1]}"
        self.stop = threading.Event()
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(connection,), daemon=True).start()

    def handle(self, connection):
        try:
            connection.recv(65536)
            if self.slow_preamble:
                for byte in self.preamble:
                    connection.sendall(bytes([byte]))
                    time.sleep(0.1)
            else:
                connection.sendall(self.preamble)
            while not self.stop.is_set() and self.forever:
                connection.sendall(b" ")
                time.sleep(0.1)
            while not self.stop.is_set() and self.silent:  # headers sent, then nothing, connection open
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            connection.close()

    def close(self):
        self.stop.set()
        self.server.close()


TRICKLES = [
    ("a status line sent byte by byte", b"HTTP/1.0 200 OK\r\nContent-Length: 5000\r\n\r\n" + b"x" * 100, False),
    ("a body with a length sent byte by byte", b"HTTP/1.0 200 OK\r\nContent-Length: 100000\r\n\r\n", True),
    ("a body that ends at the close, sent byte by byte", b"HTTP/1.0 200 OK\r\n\r\n", True),
]
# The headers arrive at once and only the body is slow. http.client drops its socket when the
# answer will close the connection, which is where the first watchdog had nothing to shut.
SLOW_BODIES = [
    ("headers at once, HTTP/1.0 body with no length", b"HTTP/1.0 200 OK\r\n\r\n"),
    ("headers at once, HTTP/1.0 body with a length", b"HTTP/1.0 200 OK\r\nContent-Length: 100000\r\n\r\n"),
    ("headers at once, HTTP/1.1 Connection close with a length", b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 100000\r\n\r\n"),
    ("headers at once, HTTP/1.1 Connection close with no length", b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n"),
    ("headers at once, HTTP/1.1 chunked", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n64\r\n"),
]


@pytest.mark.parametrize(("preamble",), [(case[1],) for case in SLOW_BODIES], ids=[case[0] for case in SLOW_BODIES])
def test_a_slow_body_after_quick_headers_cannot_hold_a_request_past_the_limit(preamble, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_TIMEOUT", 0.6)
    server = Trickle(preamble, True, slow_preamble=False)
    try:
        started = time.monotonic()
        code, output = run(server.url, capsys)
    finally:
        server.close()
    assert code == 1
    assert "step 1 health: FAILED (timed out)" in output
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize(("preamble", "forever"), [case[1:] for case in TRICKLES], ids=[case[0] for case in TRICKLES])
def test_a_server_sending_one_byte_at_a_time_cannot_hold_a_request_past_the_limit(preamble, forever, capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_TIMEOUT", 0.6)
    server = Trickle(preamble, forever)
    try:
        started = time.monotonic()
        code, output = run(server.url, capsys)
    finally:
        server.close()
    assert code == 1
    assert "step 1 health: FAILED (timed out)" in output
    assert time.monotonic() - started < 2.0


def test_a_server_sending_one_byte_at_a_time_cannot_hold_the_run_past_the_total_limit(capsys, monkeypatch):
    monkeypatch.setattr(smoke, "REQUEST_TIMEOUT", 30.0)
    monkeypatch.setattr(smoke, "TOTAL_TIMEOUT", 0.6)
    server = Trickle(b"HTTP/1.0 200 OK\r\n\r\n", True, slow_preamble=False)
    try:
        started = time.monotonic()
        code, output = run(server.url, capsys)
    finally:
        server.close()
    assert code == 1
    assert "FAILED (timed out" in output
    assert time.monotonic() - started < 2.0


def test_a_very_deep_json_body_is_a_failed_step_not_a_traceback(make_stub, capsys):
    deep = b"[" * 500_000
    for routes in (failing("POST", "/api/login", 403, deep), failing("POST", "/api/alerts", 201, deep)):
        code, output = run(make_stub(routes).url, capsys)
        assert code == 1
        assert "FAILED" in output
        assert "Traceback" not in output


def test_an_ipv6_host_is_given_to_http_client_in_brackets(monkeypatch, capsys):
    seen = []

    class Recording(http.client.HTTPConnection):
        def __init__(self, host, port=None, timeout=None):
            seen.append((host, port))
            super().__init__("127.0.0.1", 9, timeout=timeout)  # nothing listens there

    monkeypatch.setattr(smoke.http.client, "HTTPConnection", Recording)
    run("http://[::1]:8000", capsys, "--health-only", environ={})
    assert seen == [("[::1]", 8000)]


def test_a_server_that_is_not_there_fails_step_1_without_a_traceback(capsys):
    code, output = run("http://127.0.0.1:9", capsys)  # the discard port; nothing listens
    assert code == 1
    assert "step 1 health: FAILED (no usable answer (" in output
    assert "Traceback" not in output


# --- the module itself --------------------------------------------------------------------------


def test_the_module_uses_the_standard_library_only_apart_from_two_length_constants():
    tree = ast.parse((ROOT / "socboard" / "smoke.py").read_text(encoding="utf-8"))
    top_level = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else [node])
    }
    assert top_level <= set(sys.stdlib_module_names)
    inside = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("socboard")]
    assert sorted(node.module for node in inside) == ["socboard.ingest", "socboard.seed"]


def test_it_runs_as_a_module_and_checks_health_only_without_a_credential(make_stub):
    stub = make_stub()
    done = subprocess.run(
        [sys.executable, "-m", "socboard.smoke", "--health-only", stub.url],
        cwd=ROOT,
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "step 1 health: ok" in done.stdout


def test_it_exits_2_without_an_argument():
    done = subprocess.run([sys.executable, "-m", "socboard.smoke"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode == 2
