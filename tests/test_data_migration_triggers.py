"""The audit triggers and the migrations (#11, option B).

Alembic's autogenerate does not read triggers, so these tests compare `sqlite_master` rows
directly. Every database lives in a pytest temporary directory.
"""

from data_support import (
    db_url,  # noqa: F401
    isolated_database_env,  # noqa: F401
    no_default_database_file,  # noqa: F401
)
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from socboard import database, migrate
from socboard.models import metadata

TRIGGERS = ["audit_entries_no_delete", "audit_entries_no_reuse_id", "audit_entries_no_update"]


def master_rows(url, kind=None):
    """`sqlite_master` rows (type, name, tbl_name, sql), all of them or one type."""
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.exec_driver_sql(
                "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
            ).all()
    finally:
        engine.dispose()
    return [tuple(row) for row in rows if kind is None or row[0] == kind]


def test_the_three_triggers_are_present_after_migrate_and_on_audit_entries(db_url):
    migrate.upgrade(db_url)
    triggers = master_rows(db_url, "trigger")
    assert [row[1] for row in triggers] == TRIGGERS
    # A rebuild of the table drops triggers silently; the failure says so.
    assert {row[2] for row in triggers} == {"audit_entries"}, "a rebuild dropped or moved the triggers"
    for _type, name, _table, sql in triggers:
        assert sql and name in sql


def test_the_trigger_sql_holds_fixed_messages_and_the_id_reuse_condition(db_url):
    migrate.upgrade(db_url)
    by_name = {row[1]: row[3] for row in master_rows(db_url, "trigger")}
    assert "BEFORE UPDATE ON audit_entries" in by_name["audit_entries_no_update"]
    assert "BEFORE DELETE ON audit_entries" in by_name["audit_entries_no_delete"]
    reuse = by_name["audit_entries_no_reuse_id"]
    assert "BEFORE INSERT ON audit_entries" in reuse
    assert "WHEN NEW.id IS NOT NULL AND EXISTS (SELECT 1 FROM audit_entries WHERE id = NEW.id)" in reuse
    for sql in by_name.values():
        assert "RAISE(ABORT, '" in sql
        assert "NEW." not in sql.split("BEGIN", 1)[1]  # the message names no value


def test_the_triggers_are_absent_at_base(db_url):
    migrate.upgrade(db_url)
    migrate.downgrade(db_url, "base")
    assert master_rows(db_url, "trigger") == []
    names = [row[1] for row in master_rows(db_url) if not row[1].startswith("sqlite_")]
    assert names == ["alembic_version"]


def test_the_trigger_rows_are_identical_after_a_downgrade_and_upgrade_round_trip(db_url):
    migrate.upgrade(db_url)
    before_all = master_rows(db_url)
    before_triggers = master_rows(db_url, "trigger")
    assert len(before_triggers) == 3

    migrate.downgrade(db_url, "base")
    migrate.upgrade(db_url)

    assert master_rows(db_url, "trigger") == before_triggers
    assert master_rows(db_url) == before_all  # type, name, table and sql, triggers included


def test_a_second_upgrade_keeps_the_triggers_unchanged(db_url):
    migrate.upgrade(db_url)
    before = master_rows(db_url, "trigger")
    migrate.upgrade(db_url)
    assert master_rows(db_url, "trigger") == before


def test_the_models_comparison_shows_no_differences_with_the_triggers_present(db_url):
    migrate.upgrade(db_url)
    assert len(master_rows(db_url, "trigger")) == 3
    engine = database.create_db_engine(db_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            assert compare_metadata(context, metadata) == []
    finally:
        engine.dispose()
