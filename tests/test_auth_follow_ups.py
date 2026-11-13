"""Follow-ups from #13 (#104): a missing database file, hash bounds, the secret rule, NUL login."""

import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from backend_support import (
    ANALYST_ID,
    ANALYST_PASSWORD,
    ANALYST_USERNAME,
    TEST_TOKEN_SECRET,
    sqlite_url,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from socboard import auth, database, passwords, server, sessions
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


def test_with_a_missing_file_no_token_is_still_a_401_and_creates_no_file(missing_db_client):
    client, path, _ = missing_db_client
    response = client.get("/api/users")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
    assert not path.exists()


def test_with_a_missing_file_a_bad_login_body_is_still_a_422_and_creates_no_file(
    missing_db_client,
):
    client, path, _ = missing_db_client
    response = client.post("/api/login", json={"username": ANALYST_USERNAME})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert not path.exists()


def test_with_a_missing_file_health_is_200_and_creates_no_file(missing_db_client):
    client, path, _ = missing_db_client
    assert client.get("/api/health").status_code == 200
    assert not path.exists()


def test_an_existing_file_works_as_before(app_database, real_client):
    response = real_client.post(
        "/api/login", json={"username": ANALYST_USERNAME, "password": ANALYST_PASSWORD}
    )
    assert response.status_code == 200


def _state_for(url):
    return sessions.DatabaseState(database.parse_url(url))


def _make_database(path):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE marker (x)")
    connection.close()


def _assert_opens(url):
    """The engine opens the database `_make_database` wrote, not another one."""
    state = _state_for(url)
    try:
        with state.engine().connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM marker")).scalar() == 0
    finally:
        state.dispose()


def _assert_fails_and_creates_nothing(url, directory):
    state = _state_for(url)
    try:
        with pytest.raises(OperationalError) as error:
            state.engine().connect()
    finally:
        state.dispose()
    assert list(directory.iterdir()) == []
    return str(error.value)


def _spell(form, path):
    return form.format(path=path.as_posix(), encoded=quote(path.as_posix()))


FILE_SPELLINGS = [
    pytest.param("sqlite:///{path}", id="plain path"),
    pytest.param("sqlite:///{path}?uri=false", id="uri=false"),
    pytest.param("sqlite:///{path}?uri=0", id="uri=0"),
    pytest.param("sqlite:///file:{path}?uri=true", id="file URI"),
    pytest.param("sqlite:///file:{path}?uri=true&mode=rwc", id="file URI mode=rwc"),
    pytest.param("sqlite:///file://{path}?uri=true", id="file:///abs"),
    pytest.param("sqlite:///file:{encoded}?uri=true", id="file URI percent-encoded"),
    pytest.param("sqlite:///file:{path}?uri=true&cache=shared", id="file URI other parameter"),
    pytest.param("sqlite:///file:{path}?uri=true&uri=false", id="repeated uri"),
    pytest.param("sqlite:///file:{path}#frag?uri=true", id="fragment after a file path"),
    pytest.param("sqlite:///file:{path}?uri=true&mod%2565=rw%2563", id="percent-encoded mode"),
]


@pytest.mark.parametrize("form", FILE_SPELLINGS)
def test_a_missing_file_is_not_created_in_any_spelling(tmp_path, form):
    path = tmp_path / "a b.db"
    message = _assert_fails_and_creates_nothing(_spell(form, path), tmp_path)
    assert "unable to open database file" in message


@pytest.mark.parametrize("form", FILE_SPELLINGS)
def test_an_existing_file_opens_in_every_spelling(tmp_path, form):
    path = tmp_path / "a b.db"
    _make_database(path)
    _assert_opens(_spell(form, path))
    assert [entry.name for entry in tmp_path.iterdir()] == ["a b.db"]


@pytest.mark.parametrize("form", ["sqlite:///there.db", "sqlite:///file:there.db?uri=true"])
def test_a_relative_path_opens_an_existing_file_and_never_creates_one(tmp_path, monkeypatch, form):
    monkeypatch.chdir(tmp_path)
    _assert_fails_and_creates_nothing(form, tmp_path)
    _make_database(tmp_path / "there.db")
    _assert_opens(form)
    assert [entry.name for entry in tmp_path.iterdir()] == ["there.db"]


@pytest.mark.parametrize("name", ["a b.db", "a?b.db", "a#b.db", "a%b.db", "a%20b.db", "a:b.db"])
def test_a_plain_path_keeps_every_character_of_the_file_name(tmp_path, name):
    url = URL.create("sqlite", database=str(tmp_path / name))
    _assert_fails_and_creates_nothing(url, tmp_path)
    _make_database(tmp_path / name)
    _assert_opens(url)
    assert [entry.name for entry in tmp_path.iterdir()] == [name]


def test_a_file_created_after_a_failed_request_opens_without_a_restart(tmp_path):
    path = tmp_path / "late.db"
    state = _state_for(sqlite_url(path))
    try:
        with pytest.raises(OperationalError):
            state.engine().connect()
        _make_database(path)
        with state.engine().connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM marker")).scalar() == 0
    finally:
        state.dispose()


def test_the_missing_file_error_holds_neither_path_nor_url(tmp_path):
    path = tmp_path / "secret-dir-name.db"
    message = _assert_fails_and_creates_nothing(sqlite_url(path), tmp_path)
    assert "secret-dir-name" not in message and str(tmp_path) not in message


@pytest.mark.parametrize(
    "form",
    [
        pytest.param("sqlite://", id="no file name"),
        pytest.param("sqlite:///:memory:", id="plain memory"),
        pytest.param("sqlite:///file::memory:?cache=shared&uri=true", id="shared memory"),
        pytest.param("sqlite:///file:{path}?mode=memory&uri=true", id="missing file, mode=memory"),
    ],
)
@pytest.mark.filterwarnings("ignore:Selection of the SingletonThreadPool:DeprecationWarning")
def test_memory_forms_work_and_create_no_file(tmp_path, form):
    state = _state_for(_spell(form, tmp_path / "m.db"))
    try:
        with state.engine().connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar() == 1
    finally:
        state.dispose()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("mode", ["ro", "rw"])
def test_a_read_only_or_read_write_uri_of_a_missing_file_fails_and_creates_none(tmp_path, mode):
    form = "sqlite:///file:{path}?mode=" + mode + "&uri=true"
    _assert_fails_and_creates_nothing(_spell(form, tmp_path / "r.db"), tmp_path)


def test_the_connect_hook_is_added_once_per_engine_and_again_after_dispose(tmp_path):
    state = _state_for(sqlite_url(tmp_path / "x.db"))
    first = state.engine()
    assert state.engine() is first
    assert len(first.dialect.dispatch.do_connect) == 1
    state.dispose()
    second = state.engine()
    assert second is not first
    assert len(second.dialect.dispatch.do_connect) == 1
    state.dispose()


def test_a_url_that_is_not_sqlite_gets_no_connect_hook(monkeypatch):
    # No Postgres driver is installed: the engine is built on a stand-in module and never used.
    stand_in = SimpleNamespace(__version__="1.31.2", paramstyle="format")
    engine = create_engine("postgresql+pg8000://u:p@localhost/db", module=stand_in)
    monkeypatch.setattr(database, "create_db_engine", lambda url: engine)
    state = _state_for("postgresql+pg8000://u:p@localhost/db")
    assert state.engine() is engine
    assert len(engine.dialect.dispatch.do_connect) == 0


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
