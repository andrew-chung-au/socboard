"""A failed Postgres connection says nothing about the URL (#143), with no database server.

The Postgres driver's own message names the user and the database, and the 500 handler logs the
exception with its traceback, so the engine replaces a failed connection with fixed text. These
tests run in the default (SQLite) test run; the CI Postgres job shows the same end to end.
"""

import traceback

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from socboard import database

MARKERS = ("marker_user_q7", "marker_password_q7", "marker_db_q7")
POSTGRES_URL = "postgresql+psycopg://marker_user_q7:marker_password_q7@127.0.0.1:5432/marker_db_q7"


def failing_connect(dbapi):
    def connect(*args, **kwargs):
        raise dbapi.OperationalError(
            'connection failed: password authentication failed for user "marker_user_q7"'
            ' (database "marker_db_q7", password "marker_password_q7")'
        )

    return connect


def formatted(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


def test_a_postgres_engine_replaces_the_driver_error_with_fixed_text(monkeypatch):
    engine = database.create_db_engine(POSTGRES_URL)
    dbapi = engine.dialect.loaded_dbapi
    monkeypatch.setattr(dbapi, "connect", failing_connect(dbapi))
    try:
        with pytest.raises(database.DatabaseConnectError) as caught:
            engine.connect()
    finally:
        engine.dispose()
    assert str(caught.value) == database.CONNECT_FAILED_MESSAGE + " (reason: authentication)"
    assert caught.value.__suppress_context__
    text = formatted(caught.value)
    for marker in MARKERS:
        assert marker not in text, marker


def test_without_the_hook_the_driver_error_would_carry_the_markers(monkeypatch):
    engine = database.create_db_engine(POSTGRES_URL)
    event.remove(engine, "do_connect", database._connect_without_detail)
    dbapi = engine.dialect.loaded_dbapi
    monkeypatch.setattr(dbapi, "connect", failing_connect(dbapi))
    try:
        with pytest.raises(Exception) as caught:
            engine.connect()
    finally:
        engine.dispose()
    assert not isinstance(caught.value, database.DatabaseConnectError)
    assert "marker_user_q7" in formatted(caught.value)


def test_a_real_refused_postgres_connection_gives_the_fixed_text():
    engine = database.create_db_engine(
        "postgresql+psycopg://marker_user_q7:marker_password_q7@127.0.0.1:1/marker_db_q7"
    )
    try:
        with pytest.raises(database.DatabaseConnectError) as caught:
            engine.connect()
    finally:
        engine.dispose()
    text = formatted(caught.value)
    assert str(caught.value) == database.CONNECT_FAILED_MESSAGE + " (reason: refused)"
    for marker in MARKERS:
        assert marker not in text, marker


def test_a_real_unresolvable_host_gives_the_fixed_text_and_the_unknown_reason():
    engine = database.create_db_engine(
        "postgresql+psycopg://marker_user_q7:marker_password_q7@marker-host-q7.invalid:5432/marker_db_q7"
    )
    try:
        with pytest.raises(database.DatabaseConnectError) as caught:
            engine.connect()
    finally:
        engine.dispose()
    assert str(caught.value) == database.CONNECT_FAILED_MESSAGE + " (reason: unknown)"
    for marker in MARKERS + ("marker-host-q7",):
        assert marker not in formatted(caught.value), marker


def test_only_server_engines_get_the_hook(tmp_path):
    sqlite_engine = database.create_db_engine("sqlite:///" + (tmp_path / "x.db").as_posix())
    postgres_engine = database.create_db_engine(POSTGRES_URL)
    try:
        assert not event.contains(sqlite_engine, "do_connect", database._connect_without_detail)
        assert event.contains(postgres_engine, "do_connect", database._connect_without_detail)
    finally:
        sqlite_engine.dispose()
        postgres_engine.dispose()


def raising(error):
    def connect(*args, **kwargs):
        raise error

    return connect


# The driver's message as libpq and the server word it (Postgres 16); each carries the markers.
DRIVER_MESSAGES = [
    (
        'connection failed: connection to server at "127.0.0.1", port 5432 failed: FATAL:  '
        'password authentication failed for user "marker_user_q7" (password "marker_password_q7")',
        "authentication",
    ),
    (
        'connection failed: connection to server at "127.0.0.1", port 5432 failed: '
        "fe_sendauth: no password supplied marker_user_q7",
        "authentication",
    ),
    (
        'connection failed: connection to server at "127.0.0.1", port 5432 failed: FATAL:  '
        'database "marker_db_q7" does not exist',
        "database-missing",
    ),
    (
        'connection failed: connection to server at "marker_host_q7", port 5432 failed: Connection refused\n'
        "\tIs the server running on that host and accepting TCP/IP connections?",
        "refused",
    ),
    (
        'connection failed: connection to server at "marker_host_q7", port 5432 failed: timeout expired',
        "timeout",
    ),
    (
        'connection failed: connection to server at "marker_host_q7", port 5432 failed: Connection timed out',
        "timeout",
    ),
    ('could not translate host name "marker_host_q7" to address: Name or service not known', "unknown"),
    ("marker_user_q7 marker_password_q7 marker_db_q7", "unknown"),
    ("", "unknown"),
]


@pytest.mark.parametrize(("message", "reason"), DRIVER_MESSAGES)
def test_the_driver_message_gives_one_fixed_reason_and_is_never_printed(monkeypatch, message, reason):
    engine = database.create_db_engine(POSTGRES_URL)
    dbapi = engine.dialect.loaded_dbapi
    monkeypatch.setattr(dbapi, "connect", raising(dbapi.OperationalError(message)))
    try:
        with pytest.raises(database.DatabaseConnectError) as caught:
            engine.connect()
    finally:
        engine.dispose()
    assert str(caught.value) == database.CONNECT_FAILED_MESSAGE + f" (reason: {reason})"
    assert reason in database.CONNECT_FAILURE_REASONS
    for marker in MARKERS + ("marker_host_q7",):
        assert marker not in formatted(caught.value), marker


def test_an_error_that_is_not_a_driver_error_still_gives_a_fixed_reason(monkeypatch):
    engine = database.create_db_engine(POSTGRES_URL)
    dbapi = engine.dialect.loaded_dbapi
    monkeypatch.setattr(dbapi, "connect", raising(RuntimeError("marker_user_q7 marker_password_q7")))
    try:
        with pytest.raises(database.DatabaseConnectError) as caught:
            engine.connect()
    finally:
        engine.dispose()
    assert str(caught.value) == database.CONNECT_FAILED_MESSAGE + " (reason: unknown)"
    for marker in MARKERS:
        assert marker not in formatted(caught.value), marker


def test_the_connect_error_is_a_sqlalchemy_error():
    assert issubclass(database.DatabaseConnectError, SQLAlchemyError)
