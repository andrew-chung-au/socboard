"""`python -m socboard.set_password <username>` (#185): reset one existing user's password.

Every username, password and database here is synthetic. The new password comes from
`SOCBOARD_NEW_PASSWORD`; the tests call `set_password.main(environ, argv)` on a migrated, seeded
SQLite database in a temporary directory.
"""

import logging

import pytest
from data_support import (  # noqa: F401  (fixtures)
    SEED_PASSWORDS,
    empty_database_url,
    isolated_database_env,
    migrated_database_url,
    no_default_database_file,
)
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine

from socboard import database, passwords, seed, set_password
from socboard.app import create_app
from socboard.models import AuditEntry, User

VAR = set_password.PASSWORD_VAR
OLD = SEED_PASSWORDS["SOCBOARD_SEED_PASSWORD_ANALYST_A"]
NEW = "test-only-new-password-0001"
SECRET = "test-only-signing-secret-0123456789abcdef"


@pytest.fixture
def seeded_url(tmp_path):
    url = migrated_database_url(tmp_path, "reset.db")
    assert seed.main({database.ENV_NAME: url, **SEED_PASSWORDS}) == 0
    return url


def env(url, password=NEW):
    values = {database.ENV_NAME: url}
    if password is not None:
        values[VAR] = password
    return values


def users(url):
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(User.__table__).order_by(User.__table__.c.id)).all()
        return {row.username: row._asdict() for row in rows}
    finally:
        engine.dispose()


def audit_count(url):
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(AuditEntry.__table__)).scalar_one()
    finally:
        engine.dispose()


def run(url, *argv, password=NEW):
    return set_password.main(env(url, password), list(argv))


# --- a reset ------------------------------------------------------------------------------------


def test_the_new_password_works_the_old_one_does_not_and_only_that_user_changes(seeded_url, capsys):
    before = users(seeded_url)
    assert run(seeded_url, "analyst-a") == 0
    after = users(seeded_url)
    assert passwords.verify_password(NEW, after["analyst-a"]["password_hash"])
    assert not passwords.verify_password(OLD, after["analyst-a"]["password_hash"])
    changed = {key for key in after["analyst-a"] if after["analyst-a"][key] != before["analyst-a"][key]}
    assert changed == {"password_hash"}
    for other in ("analyst-b", "lead"):
        assert after[other] == before[other]
    assert list(after) == list(before)
    assert capsys.readouterr().out == "socboard.set_password: the password was changed for 1 user\n"


def test_login_over_http_refuses_the_old_password_and_accepts_the_new_one(seeded_url, tmp_path):
    assert run(seeded_url, "analyst-a") == 0
    app = create_app(
        {
            "SOCBOARD_TOKEN_SECRET": SECRET,
            database.ENV_NAME: seeded_url,
            "SOCBOARD_STATIC_DIR": (tmp_path / "no-site").as_posix(),
        }
    )
    client = TestClient(app)
    old = client.post("/api/login", json={"username": "analyst-a", "password": OLD})
    new = client.post("/api/login", json={"username": "analyst-a", "password": NEW})
    assert old.status_code == 401
    assert new.status_code == 200
    assert new.json()["user"]["username"] == "analyst-a"


def test_running_it_twice_with_the_same_password_gives_a_new_salt_and_still_works(seeded_url):
    assert run(seeded_url, "lead") == 0
    first = users(seeded_url)["lead"]["password_hash"]
    assert run(seeded_url, "lead") == 0
    second = users(seeded_url)["lead"]["password_hash"]
    assert first != second
    assert passwords.verify_password(NEW, second)


def test_a_later_seed_leaves_the_changed_password_alone(seeded_url):
    assert run(seeded_url, "lead") == 0
    changed = users(seeded_url)["lead"]["password_hash"]
    assert seed.main({database.ENV_NAME: seeded_url, **SEED_PASSWORDS}) == 0
    assert users(seeded_url)["lead"]["password_hash"] == changed


def test_it_issues_one_update_and_no_ddl_and_writes_no_audit_entry(seeded_url):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.strip().split(None, 1)[0].upper())

    event.listen(Engine, "before_cursor_execute", record)
    try:
        assert run(seeded_url, "analyst-b") == 0
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    assert statements.count("UPDATE") == 1
    assert not {"CREATE", "ALTER", "DROP", "INSERT", "DELETE"} & set(statements)
    assert audit_count(seeded_url) == 0


# --- refusals: the username ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "username",
    ["test-nobody", "", " ", " lead", "lead ", "Lead", "LEAD", "lead\x00", "le\x00ad", "\ud800", "lead\n"],
)
def test_a_missing_blank_padded_or_differently_cased_username_is_refused_and_nothing_changes(
    seeded_url, capsys, username
):
    before = users(seeded_url)
    assert run(seeded_url, username) == 2
    assert users(seeded_url) == before
    assert capsys.readouterr().err == f"socboard.set_password: {set_password.NO_SUCH_USER_MESSAGE}\n"


@pytest.mark.parametrize("arguments", [[], ["a", "b"]])
def test_a_wrong_number_of_arguments_is_a_usage_error(seeded_url, capsys, arguments):
    before = users(seeded_url)
    assert run(seeded_url, *arguments) == 2
    assert users(seeded_url) == before
    assert "usage:" in capsys.readouterr().err


# --- refusals: the password ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "password",
    [None, "", "x" * 11, "short", "test-only-new\x00-password-0001", "x" * 12 + "\x00"],
)
def test_a_missing_short_or_nul_password_is_refused_before_any_write(seeded_url, capsys, password):
    before = users(seeded_url)
    assert run(seeded_url, "lead", password=password) == 2
    assert users(seeded_url) == before
    err = capsys.readouterr().err
    assert err == f"socboard.set_password: {set_password.PASSWORD_MESSAGE}\n"
    assert VAR in err
    if password:
        assert password not in err


def test_twelve_characters_is_enough(seeded_url):
    assert run(seeded_url, "lead", password="x" * 12) == 0
    assert passwords.verify_password("x" * 12, users(seeded_url)["lead"]["password_hash"])


# --- refusals: the database ---------------------------------------------------------------------


def test_a_bad_database_url_exits_2_without_printing_it(capsys):
    url = "not-a-url-test-marker-77"
    assert set_password.main({database.ENV_NAME: url, VAR: NEW}, ["lead"]) == 2
    out = capsys.readouterr()
    assert "test-marker-77" not in out.out + out.err


def test_an_unmigrated_database_exits_1_before_the_password_is_looked_at(tmp_path, capsys):
    url = empty_database_url(tmp_path, "bare.db")
    assert set_password.main({database.ENV_NAME: url}, ["lead"]) == 1
    assert capsys.readouterr().err == f"socboard.set_password: {set_password.NOT_MIGRATED_MESSAGE}\n"


# --- nothing secret is printed or logged --------------------------------------------------------


def test_neither_the_password_the_hash_nor_the_username_is_printed_or_logged(seeded_url, capsys, caplog):
    with caplog.at_level(logging.DEBUG):
        assert run(seeded_url, "analyst-a") == 0
        for username, password in (("analyst-a", "short"), ("test-nobody-marker", NEW), ("analyst-a", None)):
            assert run(seeded_url, username, password=password) == 2
    out = capsys.readouterr()
    text = out.out + out.err + " ".join(record.getMessage() for record in caplog.records)
    stored = users(seeded_url)["analyst-a"]["password_hash"]
    for forbidden in (NEW, stored, "analyst-a", "test-nobody-marker", OLD):
        assert forbidden not in text


# --- the settings list --------------------------------------------------------------------------


def test_the_new_password_variable_is_a_secret_in_the_settings_list():
    from socboard import settings

    entry = next(s for s in settings.SETTINGS if s.name == VAR)
    assert (entry.secret, entry.deployed, entry.make_run) == (True, False, False)
