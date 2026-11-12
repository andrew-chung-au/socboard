"""DDL on `audit_entries` and its triggers (#11, option B).

The triggers cannot stop DDL, so the application guard is the only guard against it. These tests
run with the guard on. The last test records a known limit: raw-SQL DDL is not refused.
"""

import sqlite3

import pytest
from data_support import (
    isolated_database_env,  # noqa: F401
    migrated_engine,  # noqa: F401
    no_default_database_file,  # noqa: F401
)
from sqlalchemy import DDL, column, table, text
from sqlalchemy.schema import DropTable

from socboard.audit_guard import AuditImmutableError, refused_statement

TRIGGERS = ("audit_entries_no_update", "audit_entries_no_delete", "audit_entries_no_reuse_id")


def schema_names(engine):
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT type, name FROM sqlite_master").all()
    return {(row[0], row[1]) for row in rows}


def _light(name):
    return table(name, column("id"), column("detail"))


LIGHTWEIGHT_DDL = {
    "drop_table_lower": lambda: DropTable(_light("audit_entries")),
    "drop_table_upper": lambda: DropTable(_light("AUDIT_ENTRIES")),
    "drop_table_mixed": lambda: DropTable(_light("Audit_Entries")),
    "drop_table_if_exists": lambda: DropTable(_light("audit_entries"), if_exists=True),
    "drop_table_nested_alias": lambda: DropTable(_light("audit_entries").alias("x").alias("y")),
    # Another DDL construct: DDL text that does not name the table, aimed at it with against().
    "ddl_against_lightweight_table": lambda: DDL("DROP TABLE %(table)s").against(_light("audit_entries")),
    "ddl_against_upper_case_lightweight_table": lambda: DDL("SELECT 1 /* %(table)s */").against(
        _light("AUDIT_ENTRIES")
    ),
}


@pytest.mark.parametrize("name", LIGHTWEIGHT_DDL)
def test_ddl_on_a_lightweight_table_is_refused_by_the_guard_function(name):
    assert refused_statement(LIGHTWEIGHT_DDL[name]())


@pytest.mark.parametrize("name", LIGHTWEIGHT_DDL)
def test_ddl_on_a_lightweight_table_is_refused_and_the_table_remains(migrated_engine, name):  # noqa: F811
    before = schema_names(migrated_engine)
    with migrated_engine.connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(LIGHTWEIGHT_DDL[name]())
        conn.rollback()
    after = schema_names(migrated_engine)
    assert ("table", "audit_entries") in after
    assert after == before


def test_ddl_on_another_lightweight_table_is_not_refused():
    assert not refused_statement(DropTable(_light("scratch_rows")))
    assert not refused_statement(DropTable(_light("scratch_rows").alias("s")))


DROP_TRIGGER_TEXT = [
    "DROP TRIGGER audit_entries_no_update",
    "DROP TRIGGER audit_entries_no_delete",
    "DROP TRIGGER audit_entries_no_reuse_id",
    "DROP TRIGGER IF EXISTS audit_entries_no_update",
    "DROP TRIGGER AUDIT_ENTRIES_NO_UPDATE",
    'DROP TRIGGER "audit_entries_no_delete"',
]


@pytest.mark.parametrize("sql", DROP_TRIGGER_TEXT)
def test_a_ddl_construct_dropping_a_trigger_is_refused_and_the_trigger_remains(migrated_engine, sql):  # noqa: F811
    with migrated_engine.connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(DDL(sql))
        conn.rollback()
    names = {name for kind, name in schema_names(migrated_engine) if kind == "trigger"}
    assert set(TRIGGERS) <= names


def test_a_ddl_construct_creating_a_trigger_of_the_same_name_is_refused(migrated_engine):  # noqa: F811
    sql = "CREATE TRIGGER audit_entries_no_update BEFORE UPDATE ON other_table BEGIN SELECT 1; END"
    with migrated_engine.connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(DDL(sql))
        conn.rollback()


def test_ddl_text_naming_neither_the_table_nor_a_trigger_is_not_refused():
    assert not refused_statement(DDL("DROP TRIGGER some_other_trigger"))
    assert not refused_statement(DDL("DROP TABLE audit_entries_archive"))


# --- the recorded known limit --------------------------------------------------------------------

RAW_ROUTES = ["text", "exec_driver_sql", "dbapi_cursor"]


@pytest.mark.parametrize("route", RAW_ROUTES)
def test_known_limit_raw_sql_can_drop_a_trigger_and_the_guard_does_not_refuse_it(migrated_engine, route):  # noqa: F811
    """KNOWN LIMIT, RECORDED, NOT WANTED BEHAVIOUR (decision record, Trade-offs).

    The application guard does not parse raw SQL, so `DROP TRIGGER` sent as `text()`, as
    `exec_driver_sql` or through a DBAPI cursor succeeds and the trigger is gone from
    `sqlite_master`. Direct access to the database file can do the same. The test runs on a
    temporary migrated database. If a later change refuses these, this test fails so the record
    can be revisited together with the code; it must not be read as approval of the behaviour.
    """
    sql = "DROP TRIGGER audit_entries_no_update"
    assert ("trigger", "audit_entries_no_update") in schema_names(migrated_engine)
    if route == "text":
        with migrated_engine.begin() as conn:
            conn.execute(text(sql))
    elif route == "exec_driver_sql":
        with migrated_engine.begin() as conn:
            conn.exec_driver_sql(sql)
    else:
        raw = sqlite3.connect(migrated_engine.url.database)
        try:
            raw.cursor().execute(sql)
            raw.commit()
        finally:
            raw.close()
    names = schema_names(migrated_engine)
    assert ("trigger", "audit_entries_no_update") not in names
    assert ("trigger", "audit_entries_no_delete") in names  # only the one that was named
