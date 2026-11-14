"""A failed Postgres connection says nothing about the URL (#143), with no database server.

The Postgres driver's own message names the user and the database, and the 500 handler logs the
exception with its traceback, so the engine replaces a failed connection with fixed text. These
tests run in the default (SQLite) test run; the CI Postgres job shows the same end to end.
"""

import traceback

import pytest
from sqlalchemy import event

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
    assert str(caught.value) == database.CONNECT_FAILED_MESSAGE
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
    for marker in MARKERS:
        assert marker not in text, marker


def test_only_server_engines_get_the_hook(tmp_path):
    sqlite_engine = database.create_db_engine("sqlite:///" + (tmp_path / "x.db").as_posix())
    postgres_engine = database.create_db_engine(POSTGRES_URL)
    try:
        assert not event.contains(sqlite_engine, "do_connect", database._connect_without_detail)
        assert event.contains(postgres_engine, "do_connect", database._connect_without_detail)
    finally:
        sqlite_engine.dispose()
        postgres_engine.dispose()
