"""The engine settings and the conditional claim statement (#11)."""

import traceback
from datetime import datetime

import pytest
from data_support import (
    db_url,  # noqa: F401
    insert_alert,
    insert_case,
    insert_user,
    isolated_database_env,  # noqa: F401
    migrated_engine,  # noqa: F401
    no_default_database_file,  # noqa: F401
    sqlite_url,
)
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError

from socboard import database
from socboard.models import User

MARKER = "zz-marker-5c1e77"

# The statement in the decision record (#11). The claim function and its concurrency test are #16.
CLAIM = text(
    "UPDATE cases SET owner_id = :user_id "
    "WHERE id = :case_id AND owner_id IS NULL AND status <> 'Closed'"
)


# --- hide_parameters: no value in a traceback --------------------------------------------------


def _traceback_text(error):
    return "".join(traceback.format_exception(error))


def test_an_integrity_error_traceback_holds_no_parameter_value(migrated_engine):
    with migrated_engine.begin() as conn:
        insert_user(conn, username="taken")
    with pytest.raises(IntegrityError) as caught:
        with migrated_engine.begin() as conn:
            conn.execute(
                insert(User.__table__).values(
                    username="taken", display_name=MARKER, role="analyst", password_hash=MARKER
                )
            )
    assert MARKER not in _traceback_text(caught.value)
    assert MARKER not in str(caught.value)


def test_an_operational_error_traceback_holds_no_parameter_value(migrated_engine):
    with pytest.raises(OperationalError) as caught:
        with migrated_engine.begin() as conn:
            conn.execute(text("INSERT INTO no_such_table (a) VALUES (:a)"), {"a": MARKER})
    assert MARKER not in _traceback_text(caught.value)


def test_a_refused_naive_timestamp_traceback_holds_no_parameter_value(migrated_engine):
    with pytest.raises(StatementError) as caught:
        with migrated_engine.begin() as conn:
            insert_alert(conn, title=MARKER, description=MARKER, alert_timestamp=datetime(2026, 1, 1))
    assert MARKER not in _traceback_text(caught.value)


def test_the_marker_would_be_found_without_hide_parameters(db_url, migrated_engine):
    # Proves the tests above can fail: an engine without hide_parameters leaks the marker.
    from sqlalchemy import create_engine

    leaky = create_engine(db_url)
    try:
        with pytest.raises(OperationalError) as caught:
            with leaky.begin() as conn:
                conn.execute(text("INSERT INTO no_such_table (a) VALUES (:a)"), {"a": MARKER})
        assert MARKER in _traceback_text(caught.value)
    finally:
        leaky.dispose()


def test_the_application_engine_hides_parameters(migrated_engine):
    assert migrated_engine.hide_parameters is True


# --- SQLite settings ---------------------------------------------------------------------------


def test_the_busy_timeout_is_five_seconds(migrated_engine):
    with migrated_engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000
    assert database.BUSY_TIMEOUT_SECONDS == 5


def test_the_driver_default_transaction_mode_is_kept(migrated_engine):
    with migrated_engine.connect() as conn:
        raw = conn.connection.dbapi_connection
        assert raw.isolation_level == ""  # the sqlite3 default: DEFERRED, never BEGIN IMMEDIATE


def test_foreign_keys_are_on_for_every_new_connection(migrated_engine):
    first = migrated_engine.connect()
    second = migrated_engine.connect()
    try:
        assert first.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert second.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    finally:
        first.close()
        second.close()


# --- the database URL --------------------------------------------------------------------------


def test_the_default_url_is_the_repository_root_file():
    url = database.resolve_url({})
    assert url.get_backend_name() == "sqlite"
    assert url.database == str(database.REPO_ROOT / "socboard.db")


def test_the_url_comes_from_the_environment_variable(tmp_path):
    raw = sqlite_url(tmp_path / "x.db")
    assert database.resolve_url({database.ENV_NAME: raw}).database == str(tmp_path / "x.db")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not a url, secret-password-1",
        "postgresql://user:secret-password-2@host:notaport/db",
        "unknowndialect://user:secret-password-3@host/db",
    ],
)
def test_a_bad_url_is_refused_with_text_that_holds_no_value(raw):
    with pytest.raises(database.DatabaseUrlError) as caught:
        database.create_db_engine(database.resolve_url({database.ENV_NAME: raw}))
    assert "secret-password" not in str(caught.value)
    if raw:
        assert raw not in str(caught.value)
    assert "secret-password" not in _traceback_text(caught.value)


# --- the conditional update (the decision record's statement) ----------------------------------


def test_the_claim_statement_changes_one_row_then_none(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        other_id = insert_user(conn)
        case_id = insert_case(conn)
    with migrated_engine.begin() as conn:
        first = conn.execute(CLAIM, {"user_id": user_id, "case_id": case_id})
    with migrated_engine.begin() as conn:
        second = conn.execute(CLAIM, {"user_id": other_id, "case_id": case_id})
        owner = conn.exec_driver_sql("SELECT owner_id FROM cases").scalar()
    assert first.rowcount == 1
    assert second.rowcount == 0
    assert owner == user_id


def test_the_same_user_claiming_again_changes_none(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn)
    with migrated_engine.begin() as conn:
        assert conn.execute(CLAIM, {"user_id": user_id, "case_id": case_id}).rowcount == 1
    with migrated_engine.begin() as conn:
        assert conn.execute(CLAIM, {"user_id": user_id, "case_id": case_id}).rowcount == 0


def test_the_claim_statement_changes_none_on_a_closed_case_with_no_owner(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn, status="Closed", closing_reason="duplicate")
    with migrated_engine.begin() as conn:
        result = conn.execute(CLAIM, {"user_id": user_id, "case_id": case_id})
        owner = conn.exec_driver_sql("SELECT owner_id FROM cases").scalar()
    assert result.rowcount == 0
    assert owner is None


def test_the_claim_statement_changes_none_for_an_unknown_case(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
    with migrated_engine.begin() as conn:
        assert conn.execute(CLAIM, {"user_id": user_id, "case_id": 987654}).rowcount == 0


# --- the rest of the application is untouched --------------------------------------------------


def test_health_answers_with_no_database_file(client, tmp_path):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert list(tmp_path.glob("*.db")) == []  # nothing created a database file
