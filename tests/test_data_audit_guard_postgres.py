"""The audit guard reviewed for Postgres (#195, D6b), on the guard as #107 left it.

The guard (`socboard/audit_guard.py`) is the application layer; on Postgres the database layer is
migration 0002's four triggers and the owner-only rules of the two roles (#193, D6a). This file
shows, for each Postgres way to defeat the trail, which layer stops it:

- offline (no server, runs in every run): `refused_statement` on each statement as `DDL(...)`, and
  that the same text sent as `text()` is not refused (raw SQL is outside the guard);
- live on Postgres (`postgres_only`), as the table owner of D6a's fixtures, with the guard on: the
  `DDL(...)` forms the guard refuses raise `AuditImmutableError` and change nothing; the raw forms
  reach the database; and the limits the database covers for the application role.

Since #107, `refused_statement` compiles nothing and takes no dialect (human ruling at the claim of
#195), so these calls pass none.
"""

import pytest
from data_support import (  # noqa: F401  (fixtures)
    isolated_database_env,
    migrated_engine,
    no_default_database_file,
)
from sqlalchemy import DDL, event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_data_audit_triggers_postgres import (  # noqa: F401  (D6a's fixtures and helpers)
    TRIGGERS,
    app_engine,
    owner_engine,
    roles,
    rows_now,
    seeded,
    triggers,
)

import test_data_audit_triggers_postgres as d6a
from socboard.audit_guard import AuditImmutableError, refused_statement
from socboard.models import AuditEntry

POSTGRES_ONLY = pytest.mark.postgres_only("needs a Postgres server with migration 0002's triggers and D6a's two roles")

# (statement, refused by the guard as DDL(...)). The Design table of #195, re-run on the guard after #107.
DESIGN = [
    ("ALTER TABLE audit_entries DISABLE TRIGGER ALL", True),
    ("ALTER TABLE audit_entries DISABLE TRIGGER USER", True),
    ("DROP TRIGGER audit_entries_no_update ON audit_entries", True),
    ("DROP TRIGGER audit_entries_no_truncate ON audit_entries", True),
    ("DROP TABLE audit_entries", True),
    ("TRUNCATE audit_entries", True),
    ("TRUNCATE ONLY audit_entries", True),
    ("TRUNCATE public.audit_entries", True),
    ("TRUNCATE audit_entries RESTART IDENTITY", True),
    ("TRUNCATE notes, audit_entries", True),
    ("TRUNCATE cases CASCADE", False),  # names no audit table; the BEFORE TRUNCATE trigger refuses it (D6a)
    ("TRUNCATE users CASCADE", False),  # the same
    ("ALTER TABLE audit_entries ENABLE RULE r", True),
    ("ALTER TABLE audit_entries DISABLE RULE r", True),
    ("SET session_replication_role = replica", False),  # superuser or SET privilege; ENABLE ALWAYS (D6a)
    ("SET LOCAL session_replication_role = replica", False),
    ("ALTER ROLE app SET session_replication_role = replica", False),
    ("SELECT set_config('session_replication_role', 'replica', false)", False),
    ("ALTER TABLE audit_entries ENABLE REPLICA TRIGGER audit_entries_no_update", True),
    ("ALTER TABLE audit_entries OWNER TO someone", True),
    ("ALTER TABLE audit_entries RENAME TO old_entries", True),
    ("ALTER TABLE audit_entries SET SCHEMA other", True),
    ("CREATE RULE r AS ON UPDATE TO audit_entries DO INSTEAD NOTHING", True),
    ("COPY audit_entries FROM STDIN", True),
    ("CREATE VIEW v AS SELECT * FROM audit_entries", True),
    # The trigger function and the id sequence: not refused (no table or trigger name as a word).
    # Owner only (replacing or dropping the function); setval needs UPDATE on the sequence (below).
    (
        "CREATE OR REPLACE FUNCTION public.audit_entries_refuse_update() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
        False,
    ),
    ("DROP FUNCTION public.audit_entries_refuse_update() CASCADE", False),
    ("ALTER SEQUENCE audit_entries_id_seq RESTART WITH 1", False),
    ("SELECT setval('audit_entries_id_seq', 1000)", False),
    # SQLite rules: #107 removed the ATTACH rule; the sqlite_sequence rule stays.
    ("ATTACH DATABASE 'x' AS y", False),
    ("UPDATE sqlite_sequence SET seq = 0", True),
]

# The statements the guard does not refuse that the database layer covers, and the D6a test that
# shows the refusal (#195: name it, do not repeat it).
COVERED_BY_D6A = {
    "SET session_replication_role = replica": (
        "test_the_application_role_cannot_set_replica_mode_and_the_trigger_still_refuses",
        "test_a_superuser_in_replica_mode_is_still_refused_because_the_triggers_are_enabled_always",
    ),
    "TRUNCATE cases CASCADE": ("test_truncate_is_refused_and_every_table_keeps_its_rows",),
}

# The DDL(...) forms the live test sends with the guard on (#195, criterion "Statements, live").
LIVE_REFUSED = [
    "ALTER TABLE audit_entries DISABLE TRIGGER ALL",
    "DROP TRIGGER audit_entries_no_update ON audit_entries",
    "DROP TABLE audit_entries",
    "TRUNCATE audit_entries",
    "ALTER TABLE audit_entries ENABLE RULE r",
    "ALTER TABLE audit_entries DISABLE RULE r",
]


def _row(**extra):
    row = dict(case_id=1, actor_id=1, action="claimed", detail="d", at=d6a.AT)
    row.update(extra)
    return row


# --- offline: every run -------------------------------------------------------------------------


@pytest.mark.parametrize(("statement", "refused"), DESIGN, ids=[s for s, _ in DESIGN])
def test_the_guard_gives_the_design_table_result_and_never_refuses_raw_sql(statement, refused):
    assert refused_statement(DDL(statement)) is refused
    assert refused_statement(text(statement)) is False  # raw SQL is outside the guard


def test_a_postgres_insert_passes_unless_it_can_overwrite():
    assert not refused_statement(pg_insert(AuditEntry).values(**_row()))
    assert not refused_statement(pg_insert(AuditEntry).values(**_row()).on_conflict_do_nothing())  # the trigger decides
    upsert = pg_insert(AuditEntry).values(**_row()).on_conflict_do_update(index_elements=["id"], set_={"detail": "x"})
    assert refused_statement(upsert)


def test_a_postgres_only_expression_in_an_insert_is_not_refused_now_that_nothing_is_compiled():
    # Before #107 the Session hook compiled this for SQLite and refused it; #107 removed the compile.
    assert not refused_statement(pg_insert(AuditEntry).values(**_row(detail=postgresql.array(["a", "b"]))))


def test_every_statement_the_guard_leaves_to_the_database_names_a_d6a_test_that_exists():
    for statement, names in COVERED_BY_D6A.items():
        assert not refused_statement(DDL(statement)), statement
        for name in names:
            assert callable(getattr(d6a, name, None)), name


def test_an_orm_flush_does_not_reach_the_session_hook(migrated_engine):  # noqa: F811
    # The flush is guarded by the connection listener; do_orm_execute fires only for session.execute.
    calls = []

    def count(orm_execute_state):
        calls.append(orm_execute_state.statement)

    event.listen(Session, "do_orm_execute", count)
    try:
        from data_support import insert_case, insert_user

        with migrated_engine.begin() as conn:
            user_id = insert_user(conn)
            case_id = insert_case(conn)
        with Session(migrated_engine) as session:
            session.add(AuditEntry(case_id=case_id, actor_id=user_id, action="claimed", detail="flushed", at=d6a.AT))
            session.flush()
            assert calls == []
            session.rollback()
    finally:
        event.remove(Session, "do_orm_execute", count)


# --- live: Postgres -----------------------------------------------------------------------------


@POSTGRES_ONLY
@pytest.mark.parametrize("statement", LIVE_REFUSED)
@pytest.mark.parametrize("way", ["connection", "session"])
def test_the_guard_refuses_the_ddl_form_and_nothing_changes(seeded, owner_engine, statement, way):
    before = rows_now(owner_engine)
    if way == "connection":
        with owner_engine.connect() as conn:
            with pytest.raises(AuditImmutableError):
                conn.execute(DDL(statement))
            conn.rollback()
    else:
        with Session(owner_engine) as session:
            with pytest.raises(AuditImmutableError):
                session.execute(DDL(statement))
            session.rollback()
    assert triggers(owner_engine) == [(name, "A") for name in TRIGGERS]
    assert rows_now(owner_engine) == before


@POSTGRES_ONLY
@pytest.mark.parametrize(
    "statement",
    ["ALTER TABLE audit_entries DISABLE TRIGGER USER", "DROP TRIGGER audit_entries_no_update ON audit_entries"],
)
@pytest.mark.parametrize("way", ["text", "exec_driver_sql"])
def test_raw_sql_reaches_the_database_and_the_owner_can_run_it(seeded, owner_engine, statement, way):
    # The known limit: the guard does not see raw SQL, and the owner may disable or drop the triggers
    # (Deploy record, decision 8). Done in a transaction and rolled back, so nothing stays changed.
    # (`DISABLE TRIGGER ALL` also disables the foreign-key system triggers, which needs a superuser,
    # so the owner uses `DISABLE TRIGGER USER`; see the next test.)
    with owner_engine.connect() as conn:
        if way == "text":
            conn.execute(text(statement))
        else:
            conn.exec_driver_sql(statement)
        assert triggers_in(conn) != [(name, "A") for name in TRIGGERS]
        conn.rollback()
    assert triggers(owner_engine) == [(name, "A") for name in TRIGGERS]


@POSTGRES_ONLY
def test_even_the_owner_cannot_disable_all_triggers_because_the_foreign_key_ones_are_system_triggers(
    seeded, owner_engine
):
    with owner_engine.connect() as conn:
        with pytest.raises(DBAPIError):  # permission denied: "RI_ConstraintTrigger_..." is a system trigger
            conn.exec_driver_sql("ALTER TABLE audit_entries DISABLE TRIGGER ALL")
        conn.rollback()
    assert triggers(owner_engine) == [(name, "A") for name in TRIGGERS]


def triggers_in(conn):
    rows = conn.exec_driver_sql(
        "SELECT tgname, tgenabled FROM pg_trigger "
        "WHERE tgrelid = 'public.audit_entries'::regclass AND NOT tgisinternal ORDER BY tgname"
    ).all()
    return [tuple(row) for row in rows]


@POSTGRES_ONLY
def test_the_session_hook_lets_a_postgres_only_insert_reach_the_database(seeded, app_engine):
    statement = pg_insert(AuditEntry).values(**_row(detail=postgresql.array(["a", "b"])))
    with Session(app_engine) as session:
        session.execute(statement)  # not refused by the hook: Postgres stores the array as text
        assert session.execute(text("SELECT count(*) FROM audit_entries")).scalar_one() == len(seeded["before"]) + 1
        session.rollback()
    assert rows_now(app_engine) == seeded["before"]


@POSTGRES_ONLY
@pytest.mark.parametrize(
    "statement",
    ["SELECT setval('audit_entries_id_seq', 1000)", "ALTER SEQUENCE audit_entries_id_seq RESTART WITH 1"],
)
def test_the_application_role_cannot_move_the_id_sequence(seeded, app_engine, statement):
    # The guard does not see these; the database refuses them to the application role, which holds
    # only USAGE on the sequence (setval needs UPDATE; ALTER SEQUENCE needs the owner).
    with app_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.exec_driver_sql(statement)
        conn.rollback()


def _copy(engine, rows):
    """COPY rows into audit_entries through the DBAPI cursor (raw, never seen by the guard)."""
    with engine.connect() as conn:
        cursor = conn.connection.dbapi_connection.cursor()
        try:
            with cursor.copy("COPY audit_entries (id, case_id, actor_id, action, detail, at) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
        finally:
            conn.connection.dbapi_connection.rollback()


@POSTGRES_ONLY
def test_copy_fires_the_insert_trigger_so_a_reused_id_is_refused(seeded, app_engine):
    import psycopg

    row = (seeded["entry_id"], 1, 1, "claimed", "copied", "2026-11-12 09:00:00+00")
    with pytest.raises(psycopg.Error) as info:  # raw DBAPI: the driver's own error, not SQLAlchemy's
        _copy(app_engine, [row])
    assert info.value.diag.message_primary == "Audit entries cannot be overwritten."
    assert info.value.diag.sqlstate == "23000"
    assert rows_now(app_engine) == seeded["before"]


@POSTGRES_ONLY
def test_known_limit_copy_with_a_new_explicit_id_is_not_refused(seeded, app_engine):
    # A planted future id: COPY (raw) with an id above the sequence is accepted by the database.
    # Recorded as an open limit in the data-model record. The copy is rolled back here.
    with app_engine.connect() as conn:
        cursor = conn.connection.dbapi_connection.cursor()
        with cursor.copy("COPY audit_entries (id, case_id, actor_id, action, detail, at) FROM STDIN") as copy:
            copy.write_row((10**9, 1, 1, "claimed", "planted", "2026-11-12 09:00:00+00"))
        cursor.execute("SELECT count(*) FROM audit_entries WHERE id = 1000000000")
        assert cursor.fetchone()[0] == 1
        conn.connection.dbapi_connection.rollback()
    assert rows_now(app_engine) == seeded["before"]
