"""`make smoke` (#191): the argument rules of the Makefile target, and one run through make.

The refused cases stop in make before anything runs, so they cannot reach the stub server. Every
value here is synthetic.
"""

import os
import subprocess
from pathlib import Path

import pytest
from test_smoke import ENVIRON, Stub, good_routes

ROOT = Path(__file__).resolve().parents[1]


def make_smoke(*arguments, environ=None):
    return subprocess.run(
        ["make", "--no-print-directory", "smoke", *arguments],
        cwd=ROOT,
        env={**os.environ, **(environ or {})},
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture
def stub():
    server = Stub(good_routes())
    yield server
    server.close()


BAD_URLS = [
    ("a dollar sign", "http://127.0.0.1:8000/$HOME"),
    ("a dollar sign in make syntax", "http://127.0.0.1:8000/$(HOME)"),
    ("a user name and password", "http://user:pw@127.0.0.1:8000"),
    ("a query", "http://127.0.0.1:8000/?a=1"),
    ("a fragment", "http://127.0.0.1:8000/#a"),
    ("a semicolon", "http://127.0.0.1:8000;id"),
    ("a pipe", "http://127.0.0.1:8000|id"),
    ("an ampersand", "http://127.0.0.1:8000&id"),
    ("a backtick", "http://127.0.0.1:8000`id`"),
    ("a quote", "http://127.0.0.1:8000'"),
    ("a space", "http://127.0.0.1:8000 /a"),
    ("a percent sign", "http://127.0.0.1:8000/%41"),
    ("empty", ""),
]


@pytest.mark.parametrize(("url",), [(case[1],) for case in BAD_URLS], ids=[case[0] for case in BAD_URLS])
def test_make_smoke_refuses_a_bad_url_before_running_anything(url, tmp_path):
    marker = tmp_path / "ran"
    done = make_smoke(f"URL={url}", "HEALTH_ONLY=1", environ={"MARKER": str(marker)})
    assert done.returncode != 0
    assert "Usage: make smoke" in done.stderr + done.stdout
    assert not marker.exists()
    assert "step 1" not in done.stdout


@pytest.mark.parametrize("url", ["-h", "--help", "-x"])
def test_make_smoke_treats_a_url_that_starts_with_a_dash_as_a_url_not_an_option(url):
    done = make_smoke(f"URL={url}", "HEALTH_ONLY=1")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "refused" in done.stderr
    assert "usage:" not in done.stdout.lower()


def test_make_smoke_refuses_a_missing_url():
    done = make_smoke()
    assert done.returncode != 0
    assert "Usage: make smoke" in done.stderr + done.stdout


@pytest.mark.parametrize("value", ["0", "2", "yes", "11", "1 1", "true"])
def test_make_smoke_refuses_a_health_only_value_other_than_empty_or_1(value, stub):
    done = make_smoke(f"URL={stub.url}", f"HEALTH_ONLY={value}")
    assert done.returncode != 0
    assert "HEALTH_ONLY is empty or 1" in done.stderr + done.stdout
    assert stub.requests == []


def test_make_smoke_health_only_runs_through_make_without_a_credential(stub):
    done = make_smoke(f"URL={stub.url}", "HEALTH_ONLY=1", environ={"SOCBOARD_SEED_PASSWORD_LEAD": "", "SOCBOARD_INGEST_API_KEY": ""})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "step 1 health: ok" in done.stdout
    assert [request.path for request in stub.requests] == ["/api/health"]


def test_make_smoke_full_check_runs_through_make_with_the_credentials_from_the_environment(stub):
    done = make_smoke(f"URL={stub.url}", environ=ENVIRON)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "step 5 audit: ok" in done.stdout
    for value in ENVIRON.values():
        assert value not in done.stdout + done.stderr


def test_make_smoke_reports_a_failed_step_as_exit_2_and_names_the_step():
    server = Stub({})  # every route answers 404, so step 1 fails
    try:
        done = make_smoke(f"URL={server.url}", "HEALTH_ONLY=1")
    finally:
        server.close()
    assert done.returncode == 2  # make reports any failed recipe as 2; the module itself exits 1
    assert "step 1 health: FAILED (HTTP 404" in done.stdout


def test_make_smoke_without_credentials_exits_2_naming_the_variables(stub):
    done = make_smoke(f"URL={stub.url}", environ={"SOCBOARD_SEED_PASSWORD_LEAD": "", "SOCBOARD_INGEST_API_KEY": ""})
    assert done.returncode != 0
    assert "SOCBOARD_SEED_PASSWORD_LEAD" in done.stderr
    assert "SOCBOARD_INGEST_API_KEY" in done.stderr
    assert stub.requests == []
