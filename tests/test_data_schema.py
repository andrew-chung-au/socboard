"""The schema, as built by the real migrations (#11).

Every database here is made by running the migrations (never `create_all`), because Alembic's
autogenerate does not compare CHECK constraints: these inserts are the proof that the migration
has them.
"""

import pytest
from data_support import (
    ENUM_COLUMNS,
    insert_alert,
    insert_asset,
    insert_audit_row,
    insert_case,
    insert_note,
    insert_user,
    isolated_database_env,  # noqa: F401
    migrated_engine,  # noqa: F401
    no_default_database_file,  # noqa: F401
    refused_variants,
)
from sqlalchemy import delete, insert, inspect, select
from sqlalchemy.exc import IntegrityError

from socboard.ids import MAX_ID
from socboard.models import Alert, AuditEntry, Case, Note, User


def refused(engine, build):
    """Run `build(conn)` in its own transaction and expect the database to refuse it."""
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            build(conn)


# --- keys, uniqueness, ownership --------------------------------------------------------------


def test_all_six_tables_exist(migrated_engine):
    names = set(inspect(migrated_engine).get_table_names())
    assert {"users", "assets", "alerts", "cases", "notes", "audit_entries"} <= names


def test_a_64_bit_key_fits_the_column(migrated_engine):
    with migrated_engine.begin() as conn:
        conn.execute(insert(User.__table__).values(
            id=MAX_ID, username="big", display_name="Big", role="lead", password_hash="h"
        ))
        found = conn.execute(select(User.id).where(User.username == "big")).scalar_one()
    assert found == MAX_ID


def test_the_database_assigns_ascending_keys(migrated_engine):
    with migrated_engine.begin() as conn:
        first = insert_user(conn)
        second = insert_user(conn)
    assert second > first


def test_a_second_case_for_the_same_alert_is_refused(migrated_engine):
    with migrated_engine.begin() as conn:
        alert_id = insert_alert(conn)
        insert_case(conn, alert_id=alert_id)
    refused(migrated_engine, lambda conn: insert_case(conn, alert_id=alert_id))


def test_a_case_needs_an_alert(migrated_engine):
    refused(migrated_engine, lambda conn: insert_case(conn, alert_id=987654))
    with pytest.raises(IntegrityError):
        with migrated_engine.begin() as conn:
            conn.execute(insert(Case.__table__).values(alert_id=None, status="New"))


def test_an_owner_that_does_not_exist_is_refused(migrated_engine):
    refused(migrated_engine, lambda conn: insert_case(conn, owner_id=987654))


def test_an_existing_owner_is_accepted(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn, owner_id=user_id)
        owner = conn.execute(select(Case.owner_id).where(Case.id == case_id)).scalar_one()
    assert owner == user_id


def test_foreign_keys_are_on_for_a_fresh_connection(migrated_engine):
    with migrated_engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_there_is_no_assignment_table(migrated_engine):
    names = set(inspect(migrated_engine).get_table_names())
    assert not any("assign" in name for name in names)


def test_a_duplicate_username_is_refused(migrated_engine):
    with migrated_engine.begin() as conn:
        insert_user(conn, username="same")
    refused(migrated_engine, lambda conn: insert_user(conn, username="same"))


def test_a_duplicate_asset_name_is_refused(migrated_engine):
    with migrated_engine.begin() as conn:
        insert_asset(conn, name="same")
    refused(migrated_engine, lambda conn: insert_asset(conn, name="same"))


def test_an_alert_for_an_asset_outside_the_inventory_is_accepted(migrated_engine):
    with migrated_engine.begin() as conn:
        alert_id = insert_alert(conn, asset="not-in-inventory", asset_criticality="Medium")
        row = conn.execute(select(Alert.asset, Alert.asset_criticality).where(Alert.id == alert_id)).one()
    assert tuple(row) == ("not-in-inventory", "Medium")


def test_password_hash_is_not_null(migrated_engine):
    refused(migrated_engine, lambda conn: insert_user(conn, password_hash=None))


def test_the_user_repr_leaves_out_the_password_hash():
    user = User(id=1, username="alice", display_name="Alice", role="analyst", password_hash="zz-secret-hash-91")
    text = repr(user)
    assert "zz-secret-hash-91" not in text
    assert "password" not in text
    assert "alice" in text


# --- no cascades ------------------------------------------------------------------------------


def test_a_user_who_owns_a_case_cannot_be_deleted(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        insert_case(conn, owner_id=user_id)
    refused(migrated_engine, lambda conn: conn.execute(delete(User.__table__).where(User.id == user_id)))


def test_a_user_who_wrote_a_note_cannot_be_deleted(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        insert_note(conn, case_id=insert_case(conn), author_id=user_id)
    refused(migrated_engine, lambda conn: conn.execute(delete(User.__table__).where(User.id == user_id)))


def test_a_user_who_acted_in_an_audit_entry_cannot_be_deleted(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        insert_audit_row(conn, case_id=insert_case(conn), actor_id=user_id)
    refused(migrated_engine, lambda conn: conn.execute(delete(User.__table__).where(User.id == user_id)))


def test_a_case_with_notes_cannot_be_deleted(migrated_engine):
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn)
        insert_note(conn, case_id=case_id, author_id=user_id)
    refused(migrated_engine, lambda conn: conn.execute(delete(Case.__table__).where(Case.id == case_id)))


def test_a_case_with_audit_entries_cannot_be_deleted(migrated_engine):
    with migrated_engine.begin() as conn:
        case_id = insert_case(conn)
        insert_audit_row(conn, case_id=case_id, actor_id=None, action="ingested")
    refused(migrated_engine, lambda conn: conn.execute(delete(Case.__table__).where(Case.id == case_id)))


# --- text is stored byte for byte -------------------------------------------------------------

HOSTILE_VALUES = {
    "sql": "x'); DROP TABLE users; --",
    "script": "<script>alert(1)</script>",
    "newline": "line one\nline two\r\nline three",
    "long": "a" * 10_000,
    "mixed": "'; DELETE FROM alerts; -- <script>alert(1)</script>\n" + "b" * 10_000,
}


@pytest.mark.parametrize("name", sorted(HOSTILE_VALUES))
def test_text_is_stored_byte_for_byte(migrated_engine, name):
    value = HOSTILE_VALUES[name]
    with migrated_engine.begin() as conn:
        alert_id = insert_alert(conn, title=value, description=value)
        user_id = insert_user(conn)
        case_id = insert_case(conn, alert_id=alert_id)
        note_id = insert_note(conn, case_id=case_id, author_id=user_id, text=value)
        audit_id = insert_audit_row(conn, case_id=case_id, actor_id=user_id, detail=value)

    with migrated_engine.connect() as conn:
        alert = conn.execute(select(Alert.title, Alert.description).where(Alert.id == alert_id)).one()
        note = conn.execute(select(Note.text).where(Note.id == note_id)).scalar_one()
        detail = conn.execute(select(AuditEntry.detail).where(AuditEntry.id == audit_id)).scalar_one()
    assert alert.title == value
    assert alert.description == value
    assert note == value
    assert detail == value
    assert {"users", "assets", "alerts", "cases", "notes", "audit_entries"} <= set(
        inspect(migrated_engine).get_table_names()
    )


# --- enumerated values (needs the migrated database, not create_all) -------------------------

ENUM_IDS = [f"{table}.{column}" for table, column, _allowed, _build in ENUM_COLUMNS]


@pytest.mark.parametrize(("table", "column", "allowed", "build"), ENUM_COLUMNS, ids=ENUM_IDS)
def test_every_allowed_value_is_accepted(migrated_engine, table, column, allowed, build):
    for value in allowed:
        with migrated_engine.begin() as conn:
            build(conn, value)


@pytest.mark.parametrize(("table", "column", "allowed", "build"), ENUM_COLUMNS, ids=ENUM_IDS)
def test_unknown_and_wrong_case_values_are_refused(migrated_engine, table, column, allowed, build):
    variants = refused_variants(allowed)
    assert variants  # at least "bogus"
    for value in variants:
        refused(migrated_engine, lambda conn, value=value: build(conn, value))


# --- the closing reason pair ------------------------------------------------------------------


def test_a_closed_case_without_a_reason_is_refused(migrated_engine):
    refused(migrated_engine, lambda conn: insert_case(conn, status="Closed", closing_reason=None))


@pytest.mark.parametrize("status", ["New", "Investigating"])
def test_an_open_case_with_a_reason_is_refused(migrated_engine, status):
    refused(migrated_engine, lambda conn: insert_case(conn, status=status, closing_reason="duplicate"))


def test_a_closed_case_with_a_reason_and_an_open_case_without_are_accepted(migrated_engine):
    with migrated_engine.begin() as conn:
        insert_case(conn, status="Closed", closing_reason="true_positive")
        insert_case(conn, status="Investigating", closing_reason=None)


# --- the audit actor --------------------------------------------------------------------------


def test_a_null_actor_is_accepted_for_ingested(migrated_engine):
    with migrated_engine.begin() as conn:
        insert_audit_row(conn, case_id=insert_case(conn), actor_id=None, action="ingested")


@pytest.mark.parametrize("action", ["claimed", "status_changed", "note_added", "reassigned"])
def test_a_null_actor_is_refused_for_any_other_action(migrated_engine, action):
    with migrated_engine.begin() as conn:
        case_id = insert_case(conn)
    refused(migrated_engine, lambda conn: insert_audit_row(conn, case_id=case_id, actor_id=None, action=action))


def test_a_nonexistent_actor_is_refused(migrated_engine):
    with migrated_engine.begin() as conn:
        case_id = insert_case(conn)
    refused(migrated_engine, lambda conn: insert_audit_row(conn, case_id=case_id, actor_id=987654))


def test_there_is_no_system_user(migrated_engine):
    with migrated_engine.connect() as conn:
        assert conn.execute(select(User.id)).all() == []
