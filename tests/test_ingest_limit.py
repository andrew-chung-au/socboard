"""The ingest rate limit (#183): the counter, the settings, and `POST /api/alerts`.

Every key and value here is synthetic. The HTTP tests build the real app on the test database and
swap in a limiter on a fake clock, so no test sleeps.
"""

import logging
import threading

import pytest
from fastapi.testclient import TestClient
from test_ingest_api import (
    ENV_NAME,
    KEY,
    WRONG_KEY,
    assert_nothing_stored,
    payload,
    post,
    table_rows,
)

from socboard import ingest, ratelimit
from socboard.app import create_app
from socboard.auth import ConfigError
from socboard.models import Alert, AuditEntry, Case

MAX_REQUESTS = ratelimit.INGEST_MAX_REQUESTS_VAR
WINDOW = ratelimit.INGEST_WINDOW_SECONDS_VAR


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def limits(max_requests=3, window_seconds=60):
    return ratelimit.IngestLimits(max_requests=max_requests, window_seconds=window_seconds)


class Site:
    def __init__(self, app, clock):
        self.app = app
        self.clock = clock
        self.client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def make_site(app_database, monkeypatch):
    def build(max_requests=3, window_seconds=60, key=KEY):
        monkeypatch.setenv(MAX_REQUESTS, str(max_requests))
        monkeypatch.setenv(WINDOW, str(window_seconds))
        if key is None:
            monkeypatch.delenv(ENV_NAME, raising=False)
        else:
            monkeypatch.setenv(ENV_NAME, key)
        app = create_app()
        clock = FakeClock()
        app.state.ingest_limiter = ratelimit.IngestLimiter(limits(max_requests, window_seconds), clock=clock)
        return Site(app, clock)

    return build


def rows(url):
    return {model.__name__: len(table_rows(url, model)) for model in (Alert, Case, AuditEntry)}


# --- the counter --------------------------------------------------------------------------------


def test_the_limit_is_reached_at_the_configured_count_and_lifts_when_the_window_passes():
    clock = FakeClock()
    limiter = ratelimit.IngestLimiter(limits(), clock=clock)
    assert [limiter.admit() for _ in range(3)] == [None, None, None]
    assert limiter.admit() == 60
    clock.now += 59
    assert limiter.admit() == 1
    clock.now += 1
    assert limiter.admit() is None


def test_a_refused_request_is_not_counted_so_it_does_not_extend_the_wait():
    clock = FakeClock()
    limiter = ratelimit.IngestLimiter(limits(max_requests=2), clock=clock)
    assert [limiter.admit() for _ in range(2)] == [None, None]
    clock.now += 30
    assert limiter.admit() == 30
    assert limiter.admit() == 30
    clock.now += 30
    assert limiter.admit() is None


def test_requests_spread_over_the_window_slide_out_one_at_a_time():
    clock = FakeClock()
    limiter = ratelimit.IngestLimiter(limits(max_requests=2), clock=clock)
    assert limiter.admit() is None
    clock.now += 40
    assert limiter.admit() is None
    assert limiter.admit() == 20
    clock.now += 20
    assert limiter.admit() is None
    assert limiter.admit() == 40


def test_concurrent_requests_cannot_all_pass_the_check():
    limiter = ratelimit.IngestLimiter(limits(max_requests=3), clock=FakeClock())
    barrier = threading.Barrier(40)
    allowed = []

    def attempt():
        barrier.wait()
        allowed.append(limiter.admit() is None)

    threads = [threading.Thread(target=attempt) for _ in range(40)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert allowed.count(True) == 3


def test_the_state_holds_at_most_the_configured_count_of_timestamps():
    limiter = ratelimit.IngestLimiter(limits(max_requests=5), clock=FakeClock())
    for _ in range(1000):
        limiter.admit()
    assert len(limiter._times) == 5


# --- the settings -------------------------------------------------------------------------------


def test_the_defaults_are_the_documented_ones():
    resolved = ratelimit.resolve_ingest_limits({})
    assert (resolved.max_requests, resolved.window_seconds) == (60, 60)


@pytest.mark.parametrize("name", [MAX_REQUESTS, WINDOW])
@pytest.mark.parametrize("value", ["0", "-3", "abc", "", "1.5", "5 5", "٣"])
def test_a_bad_limit_is_refused_naming_the_variable_and_not_the_value(name, value):
    with pytest.raises(ConfigError) as caught:
        ingest.resolve_ingest_limiter({name: value})
    assert name in str(caught.value)
    if value:
        assert value not in str(caught.value)


def test_create_app_refuses_a_bad_limit(app_database, monkeypatch):
    monkeypatch.setenv(MAX_REQUESTS, "0")
    with pytest.raises(ConfigError, match=MAX_REQUESTS):
        create_app()


def test_the_test_environment_raises_the_limit_so_other_tests_cannot_trip_it(real_app):
    # tests/conftest.py raises the count; a test that needs the limit sets its own.
    assert real_app.state.ingest_limiter._limits.max_requests >= 1000


# --- POST /api/alerts ---------------------------------------------------------------------------


def test_above_the_rate_the_answer_is_429_with_a_code_a_fixed_message_and_retry_after(make_site):
    site = make_site()
    for _ in range(3):
        assert post(site.client).status_code == 201
    response = post(site.client)
    assert response.status_code == 429
    assert response.json() == {"code": "rate_limited", "message": ingest.RATE_LIMITED_MESSAGE}
    assert response.headers["retry-after"] == "60"


def test_refused_requests_store_nothing(make_site, app_database):
    site = make_site()
    for index in range(3):
        assert post(site.client, payload(title=f"accepted {index}")).status_code == 201
    before = rows(app_database)
    assert before == {"Alert": 3, "Case": 3, "AuditEntry": 3}
    for index in range(10):
        assert post(site.client, payload(title=f"refused {index}")).status_code == 429
    assert rows(app_database) == before


def test_the_limit_is_on_the_key_not_on_the_source_field(make_site):
    site = make_site()
    statuses = [post(site.client, payload(source=f"source-{index}")).status_code for index in range(6)]
    assert statuses == [201, 201, 201, 429, 429, 429]


def test_a_wrong_or_missing_key_is_401_first_and_uses_no_budget(make_site):
    site = make_site(max_requests=2)
    for _ in range(20):
        assert post(site.client, headers={"X-API-Key": WRONG_KEY}).status_code == 401
        assert post(site.client, headers={}).status_code == 401
    assert [post(site.client).status_code for _ in range(3)] == [201, 201, 429]
    # With the budget used up, a wrong key is still 401, not 429.
    assert post(site.client, headers={"X-API-Key": WRONG_KEY}).status_code == 401
    assert post(site.client, headers={}).status_code == 401


def test_a_request_with_the_key_counts_even_when_the_body_is_refused_with_422(make_site, app_database):
    site = make_site(max_requests=2)
    bad = payload()
    del bad["title"]
    assert [post(site.client, bad).status_code for _ in range(2)] == [422, 422]
    assert post(site.client).status_code == 429
    assert rows(app_database) == {"Alert": 0, "Case": 0, "AuditEntry": 0}


def test_over_the_rate_the_429_comes_before_the_schema_422(make_site):
    site = make_site(max_requests=1)
    assert post(site.client).status_code == 201
    bad = payload()
    del bad["title"]
    assert post(site.client, bad).status_code == 429


def test_after_the_window_requests_are_accepted_again(make_site, app_database):
    site = make_site()
    assert [post(site.client).status_code for _ in range(4)] == [201, 201, 201, 429]
    site.clock.now += 59
    assert post(site.client).status_code == 429
    site.clock.now += 1
    assert post(site.client).status_code == 201
    assert rows(app_database)["Case"] == 4


def test_a_closed_endpoint_stays_401_and_counts_nothing(make_site):
    site = make_site(key=None)
    for _ in range(10):
        assert post(site.client).status_code == 401
    assert len(site.app.state.ingest_limiter._times) == 0


def test_one_accepted_request_stores_one_set_of_rows_and_the_refused_one_none(make_site, app_database):
    site = make_site(max_requests=1)
    assert_nothing_stored(app_database)
    assert post(site.client).status_code == 201
    assert post(site.client).status_code == 429
    assert rows(app_database) == {"Alert": 1, "Case": 1, "AuditEntry": 1}


def test_no_log_line_holds_the_key_or_the_body(make_site, caplog):
    site = make_site(max_requests=1)
    marker = "test-only-body-marker-31415"
    with caplog.at_level(logging.DEBUG):
        post(site.client, payload(title=marker))
        post(site.client, payload(title=marker))
        post(site.client, payload(title=marker), headers={"X-API-Key": WRONG_KEY})
    text = " ".join(record.getMessage() for record in caplog.records)
    for forbidden in (KEY, WRONG_KEY, marker):
        assert forbidden not in text
