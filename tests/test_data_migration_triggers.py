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
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateTable

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


REBUILD_HINT = (
    "A table rebuild (Alembic batch mode on SQLite) may have dropped the triggers or AUTOINCREMENT "
    "from audit_entries; the migration that rebuilds it must re-create them (see the decision record)."
)


def audit_table_triggers(url):
    """The triggers whose `tbl_name` is `audit_entries`: (name, sql), by name."""
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            rows = conn.exec_driver_sql(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'trigger' AND tbl_name = 'audit_entries' ORDER BY name"
            ).all()
    finally:
        engine.dispose()
    return [tuple(row) for row in rows]


def audit_table_sql(url):
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as conn:
            return conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'audit_entries'"
            ).scalar()
    finally:
        engine.dispose()


def test_the_three_triggers_are_present_after_migrate_and_on_audit_entries(db_url):
    migrate.upgrade(db_url)
    triggers = audit_table_triggers(db_url)
    assert [row[0] for row in triggers] == TRIGGERS, REBUILD_HINT
    for name, sql in triggers:
        assert sql and name in sql
    # No trigger sits on any other table.
    assert [row[1] for row in master_rows(db_url, "trigger")] == TRIGGERS


def test_the_table_keeps_autoincrement_and_the_id_check(db_url):
    migrate.upgrade(db_url)
    sql = audit_table_sql(db_url)
    assert "AUTOINCREMENT" in sql.upper(), REBUILD_HINT
    assert "CONSTRAINT ck_audit_entries_id_positive CHECK (id >= 1)" in sql, REBUILD_HINT


def test_only_audit_entries_has_an_id_check(db_url):
    # The id rule for the other tables is unchanged.
    migrate.upgrade(db_url)
    for _type, name, _table, sql in master_rows(db_url, "table"):
        if name != "audit_entries" and sql:
            assert "id_positive" not in sql, name
            assert "id >= 1" not in sql, name


def test_the_model_declares_the_same_named_id_check():
    # Alembic does not compare CHECKs, so compare the text the model would create with the text
    # the migration created.
    dialect = sqlite.dialect()
    for table in metadata.tables.values():
        ddl = str(CreateTable(table).compile(dialect=dialect))
        if table.name == "audit_entries":
            assert "CONSTRAINT ck_audit_entries_id_positive CHECK (id >= 1)" in ddl
        else:
            assert "id >= 1" not in ddl, table.name


def test_the_trigger_sql_holds_fixed_messages_and_the_id_reuse_condition(db_url):
    migrate.upgrade(db_url)
    by_name = {row[1]: row[3] for row in master_rows(db_url, "trigger")}
    assert "BEFORE UPDATE ON audit_entries" in by_name["audit_entries_no_update"]
    assert "BEFORE DELETE ON audit_entries" in by_name["audit_entries_no_delete"]
    reuse = by_name["audit_entries_no_reuse_id"]
    assert "BEFORE INSERT ON audit_entries" in reuse
    assert "WHEN EXISTS (SELECT 1 FROM audit_entries WHERE id = NEW.id)" in reuse
    assert "NEW.id IS NOT NULL" not in reuse  # SQLite gives NEW.id -1 when no id is supplied
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
