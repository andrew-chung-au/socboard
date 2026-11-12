"""The signing secret, the database wiring, logs and the generated document (#13)."""

import logging
import time

import pytest
from backend_support import (
    ANALYST_PASSWORD,
    ANALYST_TOKEN,
    ANALYST_USERNAME,
    TEST_TOKEN_SECRET,
    load_contract,
    sqlite_url,
)
from fastapi.testclient import TestClient
from openapi_compare import compare_schemas, join_server_base, operation_security

from socboard import auth, errors, server
from socboard.app import create_app
from socboard.schemas import Role

SECRET_VARIABLE = "SOCBOARD_TOKEN_SECRET"


# --- the signing secret ------------------------------------------------------------------------


def test_conftest_sets_the_secret_the_test_tokens_are_signed_with(monkeypatch):
    import os

    assert os.environ[SECRET_VARIABLE] == TEST_TOKEN_SECRET
    assert len(TEST_TOKEN_SECRET) >= 32


@pytest.mark.parametrize("value", [None, "", "x" * 31, "short-test-only-value"])
def test_create_app_refuses_a_missing_empty_or_short_secret(tmp_path, value):
    environ = {"SOCBOARD_DATABASE_URL": sqlite_url(tmp_path / "x.db")}
    if value is not None:
        environ[SECRET_VARIABLE] = value
    with pytest.raises(auth.ConfigError) as error:
        create_app(environ)
    message = str(error.value)
    assert SECRET_VARIABLE in message
    if value:
        assert value not in message


def test_create_app_refuses_without_the_variable_in_the_process_environment(monkeypatch):
    monkeypatch.delenv(SECRET_VARIABLE)
    with pytest.raises(auth.ConfigError):
        create_app()


def test_thirty_two_characters_is_enough(tmp_path):
    environ = {SECRET_VARIABLE: "y" * 32, "SOCBOARD_DATABASE_URL": sqlite_url(tmp_path / "x.db")}
    assert create_app(environ).state.token_secret == "y" * 32


def test_there_is_no_default_secret():
    with pytest.raises(auth.ConfigError):
        auth.resolve_token_secret({})


@pytest.mark.parametrize("value", [None, "", "short-test-only-value"])
def test_the_server_prints_the_variable_name_and_exits_2(capsys, value):
    environ = {} if value is None else {SECRET_VARIABLE: value}
    assert server.main(environ) == 2
    err = capsys.readouterr().err
    assert SECRET_VARIABLE in err
    if value:
        assert value not in err


def test_the_server_exits_2_for_an_invalid_database_url_without_printing_it(capsys):
    raw = "not a url test-only-pw-in-url"
    environ = {SECRET_VARIABLE: TEST_TOKEN_SECRET, "SOCBOARD_DATABASE_URL": raw}
    assert server.main(environ) == 2
    err = capsys.readouterr().err
    assert "SOCBOARD_DATABASE_URL" in err
    assert "test-only-pw-in-url" not in err


# --- the database wiring -----------------------------------------------------------------------


def test_health_answers_with_no_database_file_present(tmp_path):
    path = tmp_path / "absent.db"
    app = create_app({SECRET_VARIABLE: TEST_TOKEN_SECRET, "SOCBOARD_DATABASE_URL": sqlite_url(path)})
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert not path.exists()
    assert app.state.database._engine is None  # created lazily: health asked for none


def test_the_app_uses_the_database_url_from_the_environment(real_app, app_database):
    assert real_app.state.database.url.render_as_string(hide_password=False) == app_database


# --- logs --------------------------------------------------------------------------------------


def test_no_token_password_hash_or_submitted_value_reaches_the_logs(real_client, app_database, caplog):
    from sqlalchemy import select

    from socboard import database
    from socboard.models import User

    caplog.set_level(logging.DEBUG)
    unknown_username = "test-unknown-user-zz91"
    wrong_password = "test-only-wrong-pw-zz92"
    bad_token = "zz93.notvalid"

    ok = real_client.post(
        "/api/login", json={"username": ANALYST_USERNAME, "password": ANALYST_PASSWORD}
    )
    issued = ok.json()["token"]
    real_client.post("/api/login", json={"username": ANALYST_USERNAME, "password": wrong_password})
    real_client.post("/api/login", json={"username": unknown_username, "password": wrong_password})
    real_client.get("/api/users", headers={"Authorization": f"Bearer {bad_token}"})
    real_client.get("/api/users", headers={"Authorization": f"Bearer {issued}"})
    real_client.get("/api/users", headers={"Authorization": f"Bearer {ANALYST_TOKEN}"})

    engine = database.create_db_engine(app_database)
    try:
        with engine.connect() as conn:
            hashes = list(conn.execute(select(User.password_hash)).scalars())
    finally:
        engine.dispose()

    text = "\n".join(
        f"{record.name} {record.getMessage()} {record.exc_text or ''}" for record in caplog.records
    )
    assert "Login succeeded for user id 1" in text  # the capture is not empty
    forbidden = [
        ANALYST_PASSWORD,
        wrong_password,
        ANALYST_USERNAME,
        unknown_username,
        bad_token,
        "zz93",
        issued,
        issued.split(".")[0],
        issued.split(".")[1],
        ANALYST_TOKEN.split(".")[1],
        TEST_TOKEN_SECRET,
        *hashes,
        "scrypt$",
    ]
    for value in forbidden:
        assert value not in text


# --- error codes -------------------------------------------------------------------------------


def test_invalid_credentials_is_a_contract_code():
    assert errors.INVALID_CREDENTIALS == "invalid_credentials"
    assert "`invalid_credentials`" in load_contract()["info"]["description"]


def test_the_role_type_matches_the_domain_roles():
    from typing import get_args

    from socboard.domain import ROLES

    assert get_args(Role) == ROLES


# --- the generated document against the contract -----------------------------------------------


def _implemented(contract, generated):
    contract_paths = join_server_base(contract)
    pairs = []
    for path, item in generated["paths"].items():
        for method, operation in item.items():
            if path in contract_paths and method in contract_paths[path]:
                pairs.append((path, method, contract_paths[path][method], operation))
    return pairs


def test_security_of_every_implemented_contract_operation_matches(real_app):
    contract = load_contract()
    generated = real_app.openapi()
    pairs = _implemented(contract, generated)
    names = {(path, method) for path, method, _, _ in pairs}
    assert {("/api/login", "post"), ("/api/users", "get"), ("/api/health", "get")} <= names
    for path, method, contract_operation, generated_operation in pairs:
        assert operation_security(contract, contract_operation) == operation_security(
            generated, generated_operation
        ), (path, method)
    # Stated directly as well: users needs the bearer scheme, login needs none.
    users = generated["paths"]["/api/users"]["get"]
    assert operation_security(generated, users) == [(("http", "bearer", None, None),)]
    assert operation_security(generated, generated["paths"]["/api/login"]["post"]) == []


def _schema(operation, status):
    return operation["responses"][status]["content"]["application/json"]["schema"]


def _request_schema(operation):
    return operation["requestBody"]["content"]["application/json"]["schema"]


def test_login_schemas_and_statuses_match_the_contract(real_app):
    contract = load_contract()
    generated = real_app.openapi()
    contract_op = join_server_base(contract)["/api/login"]["post"]
    generated_op = generated["paths"]["/api/login"]["post"]
    assert set(contract_op["responses"]) == set(generated_op["responses"]) == {"200", "401", "422"}
    assert compare_schemas(contract, _request_schema(contract_op), generated, _request_schema(generated_op)) == []
    assert compare_schemas(contract, _schema(contract_op, "200"), generated, _schema(generated_op, "200")) == []
    assert compare_schemas(contract, _schema(contract_op, "401"), generated, _schema(generated_op, "401")) == []
    assert generated_op["requestBody"].get("required") is True


def test_list_users_schemas_and_statuses_match_the_contract(real_app):
    contract = load_contract()
    generated = real_app.openapi()
    contract_op = join_server_base(contract)["/api/users"]["get"]
    generated_op = generated["paths"]["/api/users"]["get"]
    assert set(contract_op["responses"]) == set(generated_op["responses"]) == {"200", "401"}
    assert "requestBody" not in generated_op
    assert compare_schemas(contract, _schema(contract_op, "200"), generated, _schema(generated_op, "200")) == []


def test_the_comparison_would_notice_a_missing_user_field(real_app):
    contract = load_contract()
    generated = real_app.openapi()
    generated["components"]["schemas"]["User"]["required"].remove("role")
    contract_op = join_server_base(contract)["/api/users"]["get"]
    generated_op = generated["paths"]["/api/users"]["get"]
    assert compare_schemas(contract, _schema(contract_op, "200"), generated, _schema(generated_op, "200"))


def test_tokens_used_here_are_current():
    from socboard import tokens

    assert tokens.read_token(ANALYST_TOKEN, TEST_TOKEN_SECRET, now=time.time()) == 1
