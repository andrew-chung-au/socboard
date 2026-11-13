"""Follow-ups from #13 (#104): a missing database file, hash bounds, the secret rule, NUL login."""

import hashlib
import json
import time

import pytest
from backend_support import (
    ANALYST_ID,
    ANALYST_PASSWORD,
    ANALYST_USERNAME,
    TEST_TOKEN_SECRET,
    sqlite_url,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from socboard import auth, passwords, server, sessions
from socboard.app import create_app
from socboard.errors import INVALID_CREDENTIALS
from socboard.tokens import create_token

SECRET_VARIABLE = "SOCBOARD_TOKEN_SECRET"
SECRET_TEXT = (
    "SOCBOARD_TOKEN_SECRET must be set to at least 32 characters, "
    "not counting leading and trailing spaces."
)


# --- item 1: a request does not create a missing database file ----------------------------------


@pytest.fixture
def missing_db_client(tmp_path, monkeypatch):
    path = tmp_path / "absent" / "gone.db"
    path.parent.mkdir()
    url = sqlite_url(path)
    monkeypatch.setenv("SOCBOARD_DATABASE_URL", url)
    return TestClient(create_app(), raise_server_exceptions=False), path, url


def _assert_generic_500(response, path, url):
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    text = response.text
    assert str(path) not in text and url not in text and "absent" not in text
    assert "run make migrate" not in text and "does not exist" not in text


def test_login_against_a_missing_database_is_a_500_and_creates_no_file(missing_db_client):
    client, path, url = missing_db_client
    response = client.post(
        "/api/login", json={"username": ANALYST_USERNAME, "password": ANALYST_PASSWORD}
    )
    _assert_generic_500(response, path, url)
    assert not path.exists()


def test_a_protected_route_against_a_missing_database_is_a_500_and_creates_no_file(
    missing_db_client,
):
    client, path, url = missing_db_client
    token = create_token(ANALYST_ID, TEST_TOKEN_SECRET, now=time.time())
    response = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})
    _assert_generic_500(response, path, url)
    assert not path.exists()


def test_the_missing_file_error_text_holds_neither_path_nor_url(tmp_path):
    path = tmp_path / "secret-dir-name.db"
    state = sessions.DatabaseState(database_url(path))
    with pytest.raises(sessions.MissingDatabaseError) as error:
        state.engine()
    message = str(error.value)
    assert message == sessions.MISSING_DATABASE_MESSAGE
    assert "secret-dir-name" not in message and "sqlite" not in message
    assert state._engine is None
    assert not path.exists()


def database_url(path):
    from socboard import database

    return database.parse_url(sqlite_url(path))


@pytest.mark.parametrize("raw", ["sqlite://", "sqlite:///:memory:"])
def test_an_in_memory_or_nameless_sqlite_url_goes_to_the_driver(raw):
    from socboard import database

    state = sessions.DatabaseState(database.parse_url(raw))
    try:
        assert state.engine() is not None
    finally:
        state.dispose()


def test_an_existing_file_works_as_before(app_database, real_client):
    response = real_client.post(
        "/api/login", json={"username": ANALYST_USERNAME, "password": ANALYST_PASSWORD}
    )
    assert response.status_code == 200


def test_a_url_that_is_not_sqlite_is_not_checked(tmp_path):
    from sqlalchemy.engine import make_url

    # Not connected to: only the check is under test, and it must not look at a file for Postgres.
    sessions._refuse_missing_sqlite_file(make_url("postgresql://u:p@localhost/db"))


# --- item 4: stored scrypt parameters are bounded -------------------------------------------------

SALT = "A" * 22
DIGEST = "A" * 43


def stored(n, r, p):
    return f"scrypt$n={n},r={r},p={p}${SALT}${DIGEST}"


@pytest.mark.parametrize(
    "n,r,p",
    [(2**17, 8, 1), (2**18, 8, 1), (2**17, 8, 2), (2**18, 4, 2), (2, 1, 1)],
)
def test_parameters_inside_the_bounds_are_accepted(n, r, p):
    assert passwords.parse_hash(stored(n, r, p))[:3] == (n, r, p)


@pytest.mark.parametrize(
    "n,r,p",
    [
        pytest.param(2**19, 8, 1, id="n=2^19"),
        pytest.param(2**20, 16, 16, id="n=2^20,r=16,p=16"),
        pytest.param(2**17, 9, 1, id="r=9"),
        pytest.param(2**17, 8, 3, id="p=3"),
        pytest.param(2**18, 8, 2, id="memory 2^29"),
    ],
)
def test_parameters_outside_the_bounds_are_refused_and_never_reach_scrypt(
    monkeypatch, n, r, p
):
    value = stored(n, r, p)
    with pytest.raises(passwords.PasswordHashFormatError):
        passwords.parse_hash(value)
    calls = []
    real = hashlib.scrypt

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(passwords.hashlib, "scrypt", spy)
    assert passwords.verify_password("test-only-password-1", value) is False
    assert len(calls) == 1
    assert (calls[0]["n"], calls[0]["r"], calls[0]["p"]) == (2**17, 8, 1)


# --- item 5: the signing secret is counted after str.strip() -------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(" " * 32, id="32 spaces"),
        pytest.param("\t" * 32, id="32 tabs"),
        pytest.param("\n" * 32, id="32 newlines"),
        pytest.param(" " * 16 + "a" * 16, id="16 spaces then 16 letters"),
        pytest.param("a" * 31 + " ", id="31 letters then a space"),
        pytest.param("", id="empty"),
    ],
)
def test_a_secret_under_32_characters_after_trimming_is_refused(value):
    with pytest.raises(auth.ConfigError) as error:
        auth.resolve_token_secret({SECRET_VARIABLE: value})
    assert str(error.value) == SECRET_TEXT
    assert value not in str(error.value) or value == ""


@pytest.mark.parametrize("value", ["y" * 32, "a" * 16 + " " + "b" * 16, " " + "y" * 32 + " "])
def test_a_secret_with_32_characters_after_trimming_is_accepted_as_given(value):
    assert auth.resolve_token_secret({SECRET_VARIABLE: value}) == value


def test_the_server_exits_2_for_a_secret_of_32_spaces(capsys):
    assert server.main({SECRET_VARIABLE: " " * 32}) == 2
    err = capsys.readouterr().err
    assert SECRET_TEXT in err


# --- item 6: a NUL in the login username is no such user ------------------------------------------


@pytest.mark.parametrize("username", [ANALYST_USERNAME + "\x00", "\x00", "a\x00b"])
def test_a_nul_in_the_username_is_a_401_with_the_dummy_verify_and_no_lookup(
    real_crash_client, monkeypatch, username
):
    verified = []
    real_verify = passwords.verify_password

    def spy_verify(password, stored_hash):
        verified.append(stored_hash)
        return real_verify(password, stored_hash)

    executed = []
    real_execute = Session.execute

    def spy_execute(self, *args, **kwargs):
        executed.append(args)
        return real_execute(self, *args, **kwargs)

    monkeypatch.setattr(passwords, "verify_password", spy_verify)
    monkeypatch.setattr(Session, "execute", spy_execute)
    unknown = real_crash_client.post(
        "/api/login", json={"username": "test-nobody", "password": "test-only-wrong-pw"}
    )
    executed.clear()
    verified.clear()
    response = real_crash_client.post(
        "/api/login",
        content=json.dumps({"username": username, "password": ANALYST_PASSWORD}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401
    assert response.json() == unknown.json()
    assert response.json()["code"] == INVALID_CREDENTIALS
    assert verified == [passwords.DUMMY_HASH]
    assert executed == []


def test_a_password_with_a_nul_is_still_hashed_and_compared(real_crash_client):
    response = real_crash_client.post(
        "/api/login", json={"username": ANALYST_USERNAME, "password": "test-only\x00analyst-pw-1"}
    )
    assert response.status_code == 401
    stored_hash = passwords.hash_password("pw\x00x")
    assert passwords.verify_password("pw\x00x", stored_hash)
    assert not passwords.verify_password("pw\x00y", stored_hash)
