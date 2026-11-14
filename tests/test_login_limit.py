"""Limits on failed logins (#178): the counters, the client address, and `POST /api/login`.

Every address, username and password here is synthetic. The HTTP tests build the app with
`create_app()` on the test database and swap in a limiter on a fake clock, so no test sleeps.
"""

import logging

import pytest
from backend_support import ANALYST_PASSWORD, ANALYST_USERNAME, LEAD_PASSWORD, LEAD_USERNAME
from fastapi.testclient import TestClient

from socboard import auth, passwords, ratelimit
from socboard.app import create_app
from socboard.auth import ConfigError

# Documentation-range addresses (RFC 5737): never real hosts.
ATTACKER = "203.0.113.9"
REVIEWER = "198.51.100.20"
PROXY = "10.0.0.2"
WRONG = "test-only-wrong-pw"

FAILURES = ratelimit.MAX_FAILURES_VAR
CLIENT_FAILURES = ratelimit.CLIENT_MAX_FAILURES_VAR
WINDOW = ratelimit.WINDOW_SECONDS_VAR
TRACKED = ratelimit.MAX_TRACKED_VAR
PROXIES = ratelimit.TRUSTED_PROXIES_VAR


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def limits(**overrides):
    values = {
        "max_failures": 3,
        "client_max_failures": 6,
        "window_seconds": 60,
        "max_tracked": 100,
        "trusted_proxies": frozenset(),
    }
    values.update(overrides)
    return ratelimit.LoginLimits(**values)


class Site:
    """The real app on the test database, with a limiter on a fake clock."""

    def __init__(self, app, clock):
        self.app = app
        self.clock = clock

    def client(self, address):
        return TestClient(self.app, client=(address, 50000))

    @property
    def limiter(self):
        return self.app.state.login_guard.limiter


@pytest.fixture
def make_site(app_database, monkeypatch):
    def build(**env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        app = create_app()
        clock = FakeClock()
        guard = app.state.login_guard
        resolved = ratelimit.resolve_limits(env)
        app.state.login_guard = auth.LoginGuard(
            ratelimit.LoginLimiter(resolved, clock=clock), guard.trusted_proxies
        )
        return Site(app, clock)

    return build


def login(client, username, password, **kwargs):
    return client.post("/api/login", json={"username": username, "password": password}, **kwargs)


def fail(client, username=ANALYST_USERNAME, times=1):
    for _ in range(times):
        assert login(client, username, WRONG).status_code == 401


# --- the counters -------------------------------------------------------------------------------


def test_the_limit_is_reached_at_the_configured_count_and_lifts_when_the_window_passes():
    clock = FakeClock()
    limiter = ratelimit.LoginLimiter(limits(), clock=clock)
    for _ in range(2):
        limiter.record_failure("a", "x")
    assert limiter.retry_after("a", "x") is None
    limiter.record_failure("a", "x")
    assert limiter.retry_after("a", "x") == 60
    clock.now += 59
    assert limiter.retry_after("a", "x") == 1
    clock.now += 1
    assert limiter.retry_after("a", "x") is None


def test_the_username_key_ignores_case_and_edge_spaces_and_differs_by_client():
    limiter = ratelimit.LoginLimiter(limits(max_failures=2), clock=FakeClock())
    limiter.record_failure("a", "Test-User")
    limiter.record_failure("a", "  test-user ")
    assert limiter.retry_after("a", "TEST-USER") is not None
    assert limiter.retry_after("b", "test-user") is None


def test_the_client_counter_spans_usernames():
    limiter = ratelimit.LoginLimiter(limits(client_max_failures=4), clock=FakeClock())
    for index in range(4):
        limiter.record_failure("a", f"name-{index}")
    assert limiter.retry_after("a", "name-fresh") is not None
    assert limiter.retry_after("b", "name-fresh") is None


def test_the_table_is_bounded_and_every_key_is_a_fixed_size_digest():
    limiter = ratelimit.LoginLimiter(limits(max_tracked=10), clock=FakeClock())
    for index in range(500):
        limiter.record_failure(f"192.0.2.{index % 250}", f"made-up-{index}-" + "x" * (index * 7))
        assert limiter.tracked_keys() <= 10
    assert limiter.tracked_keys() == 10
    assert all(len(key) == 32 for key in limiter._failures)


def test_a_dropped_counter_only_loses_its_count():
    limiter = ratelimit.LoginLimiter(limits(max_failures=1, max_tracked=2), clock=FakeClock())
    limiter.record_failure("a", "one")
    assert limiter.retry_after("a", "one") is not None
    limiter.record_failure("b", "two")
    limiter.record_failure("c", "three")
    assert limiter.retry_after("a", "one") is None


# --- the client address -------------------------------------------------------------------------


TRUSTED = frozenset({PROXY})


@pytest.mark.parametrize(
    ("peer", "forwarded", "trusted", "expected"),
    [
        (None, ["1.2.3.4"], TRUSTED, "unknown"),
        (ATTACKER, ["198.51.100.7"], frozenset(), ATTACKER),
        (ATTACKER, ["198.51.100.7"], TRUSTED, ATTACKER),
        (PROXY, [], TRUSTED, PROXY),
        (PROXY, ["198.51.100.7"], TRUSTED, "198.51.100.7"),
        (PROXY, ["9.9.9.9, 198.51.100.7"], TRUSTED, "198.51.100.7"),
        (PROXY, ["9.9.9.9", "198.51.100.7"], TRUSTED, "198.51.100.7"),
        (PROXY, ["198.51.100.7, " + PROXY], TRUSTED, "198.51.100.7"),
        (PROXY, ["not-an-address"], TRUSTED, PROXY),
        (PROXY, ["2001:DB8:0:0:0:0:0:1"], TRUSTED, "2001:db8::1"),
        ("testclient", [], frozenset(), "testclient"),
    ],
)
def test_the_client_address_is_read_in_one_place(peer, forwarded, trusted, expected):
    assert ratelimit.client_address(peer, forwarded, trusted) == expected


# --- the settings -------------------------------------------------------------------------------


def test_the_defaults_are_the_documented_ones():
    resolved = ratelimit.resolve_limits({})
    assert (resolved.max_failures, resolved.client_max_failures) == (5, 20)
    assert (resolved.window_seconds, resolved.max_tracked) == (300, 10000)
    assert resolved.trusted_proxies == frozenset()


@pytest.mark.parametrize("name", [FAILURES, CLIENT_FAILURES, WINDOW, TRACKED])
@pytest.mark.parametrize("value", ["0", "-3", "abc", "", "1.5", "5 5", "٣"])
def test_a_bad_limit_is_refused_naming_the_variable_and_not_the_value(name, value):
    with pytest.raises(ConfigError) as caught:
        auth.resolve_login_guard({name: value})
    assert name in str(caught.value)
    if value:
        assert value not in str(caught.value)


@pytest.mark.parametrize("value", ["not-an-address", "10.0.0.2, oops", "10.0.0.0/8"])
def test_a_bad_proxy_list_is_refused_naming_the_variable_and_not_the_value(value):
    with pytest.raises(ConfigError) as caught:
        auth.resolve_login_guard({PROXIES: value})
    assert PROXIES in str(caught.value)
    assert value not in str(caught.value)


def test_an_empty_or_trailing_comma_proxy_list_is_accepted():
    assert auth.resolve_login_guard({PROXIES: ""}).trusted_proxies == frozenset()
    assert auth.resolve_login_guard({PROXIES: " 10.0.0.2 , "}).trusted_proxies == {PROXY}


def test_create_app_refuses_a_bad_limit(app_database, monkeypatch):
    monkeypatch.setenv(FAILURES, "0")
    with pytest.raises(ConfigError, match=FAILURES):
        create_app()


# --- POST /api/login ----------------------------------------------------------------------------


ENV = {FAILURES: "3", CLIENT_FAILURES: "6", WINDOW: "60"}


def test_the_limited_answer_is_429_with_a_code_a_fixed_message_and_retry_after(make_site):
    site = make_site(**ENV)
    client = site.client(ATTACKER)
    fail(client, times=3)
    response = login(client, ANALYST_USERNAME, WRONG)
    assert response.status_code == 429
    assert response.json() == {"code": "rate_limited", "message": auth.RATE_LIMITED_MESSAGE}
    assert response.headers["retry-after"] == "60"
    assert set(response.json()) == {"code", "message"}


def test_a_real_and_a_made_up_username_get_the_same_answer_across_the_limit(make_site):
    site = make_site(**ENV)
    client = site.client(ATTACKER)
    answers = {}
    for name in (ANALYST_USERNAME, "test-nobody"):
        statuses = [login(client, name, WRONG).status_code for _ in range(5)]
        last = login(client, name, WRONG)
        answers[name] = (statuses, last.status_code, last.content, dict(last.headers))
    assert answers[ANALYST_USERNAME] == answers["test-nobody"]
    assert answers["test-nobody"][0] == [401, 401, 401, 429, 429]


def test_an_outsider_cannot_lock_a_reviewer_out_from_another_address(make_site):
    site = make_site(**ENV)
    attacker = site.client(ATTACKER)
    reviewer = site.client(REVIEWER)
    fail(attacker, ANALYST_USERNAME, times=3)
    fail(attacker, LEAD_USERNAME, times=3)
    assert login(attacker, ANALYST_USERNAME, WRONG).status_code == 429
    assert login(reviewer, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 200
    assert login(reviewer, LEAD_USERNAME, LEAD_PASSWORD).status_code == 200


def test_a_correct_password_is_refused_while_limited_and_accepted_after_the_window(make_site):
    site = make_site(**ENV)
    client = site.client(ATTACKER)
    fail(client, times=3)
    assert login(client, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 429
    site.clock.now += 59
    assert login(client, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 429
    site.clock.now += 1
    assert login(client, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 200


def test_one_address_rotating_usernames_reaches_the_client_limit(make_site):
    site = make_site(**ENV)
    client = site.client(ATTACKER)
    for index in range(6):
        fail(client, f"test-nobody-{index}")
    assert login(client, "test-nobody-fresh", WRONG).status_code == 429
    assert login(client, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 429
    assert login(site.client(REVIEWER), ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 200


def test_the_limit_is_checked_before_any_password_work(make_site, monkeypatch):
    site = make_site(**ENV)
    client = site.client(ATTACKER)
    fail(client, ANALYST_USERNAME, times=3)
    fail(client, "test-nobody", times=3)
    calls = []

    def spy(password, stored):
        calls.append(stored)
        return False

    monkeypatch.setattr(passwords, "verify_password", spy)
    for name in (ANALYST_USERNAME, "test-nobody"):
        assert login(client, name, ANALYST_PASSWORD).status_code == 429
    assert calls == []


def test_a_successful_login_counts_nothing_and_clears_nothing(make_site):
    site = make_site(**ENV)
    client = site.client(ATTACKER)
    fail(client, times=2)
    for _ in range(5):
        assert login(client, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 200
    fail(client, times=1)
    assert login(client, ANALYST_USERNAME, WRONG).status_code == 429
    other = site.client(REVIEWER)
    fail(other, times=2)
    assert login(other, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 200
    assert login(other, ANALYST_USERNAME, WRONG).status_code == 401


def test_a_schema_invalid_body_counts_nothing(make_site):
    site = make_site(**ENV)
    client = site.client(ATTACKER)
    for _ in range(10):
        assert client.post("/api/login", json={"username": ANALYST_USERNAME}).status_code == 422
    assert site.limiter.tracked_keys() == 0


def test_a_username_flood_keeps_the_table_bounded(make_site):
    site = make_site(**{**ENV, TRACKED: "6", CLIENT_FAILURES: "1000"})
    client = site.client(ATTACKER)
    for index in range(10):
        fail(client, f"test-nobody-{index}-" + "x" * 5000)
        assert site.limiter.tracked_keys() <= 6


def test_a_forged_forwarding_header_from_an_untrusted_peer_cannot_choose_a_bucket(make_site):
    site = make_site(**ENV)
    attacker = site.client(ATTACKER)
    reviewer = site.client(REVIEWER)
    # Claiming to be the reviewer does not move the failures into the reviewer's bucket.
    for _ in range(3):
        response = login(attacker, ANALYST_USERNAME, WRONG, headers={"X-Forwarded-For": REVIEWER})
        assert response.status_code == 401
    assert login(reviewer, ANALYST_USERNAME, ANALYST_PASSWORD).status_code == 200
    # Changing the header on every request does not escape the attacker's own bucket either.
    for index in range(3):
        response = login(attacker, ANALYST_USERNAME, WRONG, headers={"X-Forwarded-For": f"192.0.2.{index}"})
        assert response.status_code == 429


def test_behind_a_trusted_proxy_the_client_is_the_rightmost_untrusted_entry(make_site):
    site = make_site(**ENV, **{PROXIES: PROXY})
    proxy = site.client(PROXY)
    for index in range(3):
        forged = f"192.0.2.{index}, {ATTACKER}"
        response = login(proxy, ANALYST_USERNAME, WRONG, headers={"X-Forwarded-For": forged})
        assert response.status_code == 401
    # The forged left side changes nothing: the same client, still limited.
    limited = login(proxy, ANALYST_USERNAME, WRONG, headers={"X-Forwarded-For": f"192.0.2.99, {ATTACKER}"})
    assert limited.status_code == 429
    # Another client behind the same proxy is not limited.
    other = login(proxy, ANALYST_USERNAME, ANALYST_PASSWORD, headers={"X-Forwarded-For": REVIEWER})
    assert other.status_code == 200


def test_a_trusted_proxy_list_does_not_make_other_peers_trusted(make_site):
    site = make_site(**ENV, **{PROXIES: PROXY})
    attacker = site.client(ATTACKER)
    for index in range(3):
        fail_response = login(attacker, ANALYST_USERNAME, WRONG, headers={"X-Forwarded-For": f"192.0.2.{index}"})
        assert fail_response.status_code == 401
    assert login(attacker, ANALYST_USERNAME, WRONG).status_code == 429


def test_without_a_proxy_list_every_client_behind_a_proxy_shares_its_bucket(make_site):
    # The documented risk: an unlisted proxy makes every client look like the proxy.
    site = make_site(**ENV)
    proxy = site.client(PROXY)
    for index in range(3):
        login(proxy, ANALYST_USERNAME, WRONG, headers={"X-Forwarded-For": f"192.0.2.{index}"})
    response = login(proxy, ANALYST_USERNAME, ANALYST_PASSWORD, headers={"X-Forwarded-For": REVIEWER})
    assert response.status_code == 429


def test_no_log_line_holds_a_username_an_address_or_a_password(make_site, caplog):
    site = make_site(**ENV)
    client = site.client(ATTACKER)
    secret_name = "test-only-secret-name"
    with caplog.at_level(logging.DEBUG):
        for _ in range(4):
            login(client, secret_name, WRONG)
    text = " ".join(record.getMessage() for record in caplog.records)
    for forbidden in (secret_name, ATTACKER, WRONG):
        assert forbidden not in text


def test_the_test_app_default_limits_are_the_documented_ones(real_app):
    limiter = real_app.state.login_guard.limiter
    assert limiter._limits == ratelimit.resolve_limits({})
