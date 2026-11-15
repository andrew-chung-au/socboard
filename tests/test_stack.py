"""The live Compose stack (#229, D12): what only a real proxy, image and database can show.

The tests marked `stack_only` run with `SOCBOARD_BASE_URL` set (`make test-stack`) and are deselected, and
counted in the run header, without it. The others run on every `make test`: the URL rule, the schema
file loader and its refusals, the marker. A live run adds cases and uses up the ingest key's
allowance for the window; the ingest test runs last for that reason.
"""

import copy
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
import stack_support as stack
from backend_support import load_contract
from test_contract_operations import operation_differences

from socboard.app import create_app

ROOT = Path(__file__).resolve().parents[1]
WRONG = "wrong-password-for-the-limit-test"
LIMIT_FAILURES = 5  # the default of SOCBOARD_LOGIN_MAX_FAILURES; compose.yaml sets no override


# --- the address rule ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["http://127.0.0.1:8080", "http://localhost:8080/", "http://[::1]:8080", "http://127.0.0.1"]
)
def test_a_loopback_http_address_is_accepted(value):
    assert stack.checked_url("SOCBOARD_BASE_URL", value) == value.rstrip("/")


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:8080",
        "http://example.com",
        "http://10.0.0.5:8080",
        "http://0.0.0.0:8080",
        "http://user:secret@127.0.0.1:8080",
        "http://127.0.0.1:8080/api",
        "http://127.0.0.1:8080?x=1",
        "http://127.0.0.1:8080/?x=1",
        "http://127.0.0.1:8080#top",
        "http://127.0.0.1:99999",
        "http://127.0.0.1:port",
        " http://127.0.0.1:8080",
        "http://127.0.0.1:8080 ",
        "http://127.0.0.1 :8080",
        "127.0.0.1:8080",
        "ftp://127.0.0.1",
    ],
)
def test_any_other_address_is_refused_without_printing_it(value):
    with pytest.raises(stack.StackSettingError) as refused:
        stack.base_url({"SOCBOARD_BASE_URL": value})
    assert "SOCBOARD_BASE_URL" in str(refused.value)
    assert value.strip() not in str(refused.value)


def test_an_unset_or_empty_address_means_an_in_process_run():
    assert stack.base_url({}) is None and stack.base_url({"SOCBOARD_BASE_URL": ""}) is None
    assert stack.is_live({}) is False


@pytest.mark.parametrize("environ", [{}, {"STACK_SCHEMA_FILE": ""}, {"STACK_SCHEMA_FILE": "   "}])
def test_a_missing_variable_fails_naming_it(environ):
    with pytest.raises(stack.StackSettingError, match="STACK_SCHEMA_FILE"):
        stack.required("STACK_SCHEMA_FILE", environ)


# --- the schema check, and its refusals ---------------------------------------------------------


def write_schema(tmp_path, schema):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    return {"STACK_SCHEMA_FILE": str(path)}


def image_like_schema():
    # The test stands in for the image: the same call the image's own code makes.
    return create_app({"SOCBOARD_TOKEN_SECRET": "test-only-stack-secret-0123456789abcdef"}).openapi()


def test_the_schema_file_of_an_unmodified_app_has_no_difference_from_the_contract(tmp_path):
    schema = stack.load_schema_file(write_schema(tmp_path, image_like_schema()))
    assert operation_differences(schema, load_contract()) == []


def first_operation(schema):
    path = next(iter(schema["paths"]))
    method = next(iter(schema["paths"][path]))
    return path, method


def test_a_schema_with_a_path_removed_is_reported(tmp_path):
    schema = copy.deepcopy(image_like_schema())
    path, _ = first_operation(schema)
    del schema["paths"][path]
    found = operation_differences(stack.load_schema_file(write_schema(tmp_path, schema)), load_contract())
    assert any("only in openapi.yaml" in line and path in line for line in found)


def test_a_schema_with_a_method_removed_is_reported(tmp_path):
    schema = copy.deepcopy(image_like_schema())
    path, method = first_operation(schema)
    schema["paths"][path] = {**schema["paths"][path]}
    del schema["paths"][path][method]
    found = operation_differences(stack.load_schema_file(write_schema(tmp_path, schema)), load_contract())
    assert any("only in openapi.yaml" in line for line in found)


def test_a_schema_with_an_operation_id_changed_is_reported(tmp_path):
    schema = copy.deepcopy(image_like_schema())
    path, method = first_operation(schema)
    schema["paths"][path][method]["operationId"] = "somethingElse"
    found = operation_differences(stack.load_schema_file(write_schema(tmp_path, schema)), load_contract())
    assert any("operationId differs" in line for line in found)


@pytest.mark.parametrize("content", ["", "not json", "[]", '{"paths": []}', '{"info": {}}'])
def test_a_schema_file_that_is_not_a_schema_fails_naming_the_variable(tmp_path, content):
    path = tmp_path / "schema.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(stack.StackSettingError) as refused:
        stack.load_schema_file({"STACK_SCHEMA_FILE": str(path)})
    assert "STACK_SCHEMA_FILE" in str(refused.value) and str(path) not in str(refused.value)


def test_a_missing_schema_file_fails_naming_the_variable(tmp_path):
    with pytest.raises(stack.StackSettingError, match="STACK_SCHEMA_FILE"):
        stack.load_schema_file({"STACK_SCHEMA_FILE": str(tmp_path / "absent.json")})


# --- the marker ---------------------------------------------------------------------------------

STACK_TESTS = len(re.findall(r"^@pytest\.mark\.stack_only\(", Path(__file__).read_text(encoding="utf-8"), re.M))


def collect(**environ):
    env = {k: v for k, v in os.environ.items() if k not in ("SOCBOARD_BASE_URL", "SOCBOARD_TEST_POSTGRES_URL")}
    env.update(environ)
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_stack.py", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return done


def test_the_stack_tests_are_deselected_and_counted_without_a_stack():
    assert STACK_TESTS == 4
    done = collect()
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"stack_only: {STACK_TESTS} marked, {STACK_TESTS} deselected" in done.stdout
    assert "forwarded_for" not in done.stdout


def test_the_stack_tests_are_collected_with_a_stack_address():
    done = collect(SOCBOARD_BASE_URL="http://127.0.0.1:1")
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"stack_only: {STACK_TESTS} marked, 0 deselected" in done.stdout
    assert done.stdout.count("forwarded_for") == 3 and "test_the_built_images_operations" in done.stdout


def test_a_bad_stack_address_stops_the_run_without_printing_it():
    done = collect(SOCBOARD_BASE_URL="http://203.0.113.9:8080")
    assert done.returncode != 0
    assert "SOCBOARD_BASE_URL" in done.stdout + done.stderr and "203.0.113.9" not in done.stdout + done.stderr


def test_a_stack_marker_without_a_reason_stops_the_run():
    import conftest

    class Marker:
        args = ()
        kwargs = {}

    class Item:
        nodeid = "tests/x.py::test_y"

        def iter_markers(self, name):
            return iter([Marker()])

    with pytest.raises(pytest.UsageError, match="stack_only needs a one-line reason"):
        conftest._marked([Item()], "stack_only")


# --- the forwarded-address rule through the proxy (live only) -----------------------------------


def limited_answers(client, username):
    """Six failed logins, each with a different forged forwarded address."""
    answers = []
    for number in range(LIMIT_FAILURES + 1):
        answers.append(
            client.post(
                "/api/login",
                json={"username": username, "password": WRONG},
                headers={"X-Forwarded-For": f"203.0.113.{number + 1}"},
            )
        )
    return answers


def assert_bucket_held(answers):
    assert [answer.status_code for answer in answers] == [401] * LIMIT_FAILURES + [429]
    assert answers[0].json()["code"] == "invalid_credentials"
    assert answers[-1].json()["code"] == "rate_limited"
    assert int(answers[-1].headers["Retry-After"]) >= 1


def lead_can_log_in(client):
    response = client.post(
        "/api/login", json={"username": "lead", "password": stack.required("SOCBOARD_SEED_PASSWORD_LEAD")}
    )
    return response.status_code == 200


@pytest.mark.stack_only("needs the proxy in the path: a forged forwarded address must not choose a login bucket")
def test_a_forged_forwarded_for_cannot_choose_a_login_bucket_through_the_proxy():
    with stack.live_client() as client:
        # Different forged addresses would be different buckets if the header chose one: the 429 shows it does not.
        assert_bucket_held(limited_answers(client, f"nobody-{secrets.token_hex(6)}"))
        assert lead_can_log_in(client)  # a real user is not limited by it


@pytest.mark.stack_only("needs the app's own published port: a peer that is not the proxy has its header ignored")
def test_a_forged_forwarded_for_cannot_choose_a_login_bucket_on_the_apps_own_port():
    url = stack.checked_url(stack.APP_URL_VAR, stack.required(stack.APP_URL_VAR))
    with stack.live_client(url=url) as client:
        assert_bucket_held(limited_answers(client, f"nobody-{secrets.token_hex(6)}"))
        assert lead_can_log_in(client)


@pytest.mark.stack_only("compares the built image's own schema, fetched from the running app container")
def test_the_built_images_operations_are_the_contracts():
    differences = operation_differences(stack.load_schema_file(), load_contract())
    assert differences == [], "\n".join(differences)


ALERT = {
    "source": "siem-test",
    "type": "failed_login",
    "asset": "kiosk-02",
    "timestamp": "2026-11-12T08:00:00Z",
    "title": "Synthetic limit alert",
    "description": "Synthetic.",
}


@pytest.mark.stack_only("uses up the ingest key's allowance for the window, so it runs last; the limit is the key's, not an address's")
def test_a_forged_forwarded_for_cannot_give_the_ingest_key_a_fresh_allowance():
    key = stack.required("SOCBOARD_INGEST_API_KEY")
    with stack.live_client() as client:
        token = client.post(
            "/api/login", json={"username": "lead", "password": stack.required("SOCBOARD_SEED_PASSWORD_LEAD")}
        ).json()["token"]
        lead = {"Authorization": f"Bearer {token}"}
        before = {case["id"] for case in client.get("/api/queue", headers=lead).json()["cases"]}
        stored, refused = 0, None
        for number in range(70):  # the default allowance is 60 a window; one forged address each time
            answer = client.post(
                "/api/alerts", headers={"X-API-Key": key, "X-Forwarded-For": f"198.51.100.{number % 250 + 1}"}, json=ALERT
            )
            if answer.status_code == 429:
                refused = answer
                break
            assert answer.status_code == 201
            stored += 1
        assert refused is not None, "70 requests with a valid key were never limited"
        assert refused.json()["code"] == "rate_limited" and int(refused.headers["Retry-After"]) >= 1
        after = {case["id"] for case in client.get("/api/queue", headers=lead).json()["cases"]}
        assert len(after - before) == stored  # the refused requests stored nothing
