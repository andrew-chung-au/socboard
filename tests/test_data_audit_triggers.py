"""The database itself keeps `audit_entries` append-only (#11, option B).

Every test here runs on a database built by the real migrations (not `create_all`, which has no
triggers) with the application guard's four listeners switched off, so a refusal cannot come from
the application layer. Each route must raise a database error that carries the trigger's fixed
message, never `AuditImmutableError`. Each attempt is rolled back, and the rows are re-read on a
new connection and are unchanged (for DELETE, still present).

The listeners are removed and restored by `guards_disabled()`, and a test proves they come back.
"""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from data_support import (
    insert_audit_row,
    insert_case,
    insert_user,
    isolated_database_env,  # noqa: F401
    migrated_engine,  # noqa: F401
    no_default_database_file,  # noqa: F401
)
from sqlalchemy import (
    column,
    delete,
    event,
    insert,
    lambda_stmt,
    literal,
    select,
    table,
    text,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, aliased

from socboard import audit_guard, models
from socboard.audit import append_audit
from socboard.audit_guard import AuditImmutableError
from socboard.models import AuditEntry

AUDIT = AuditEntry.__table__

# The fixed trigger messages (migration 0001). None of them holds a value.
CHANGED = "Audit entries cannot be changed."
REMOVED = "Audit entries cannot be removed."
OVERWRITTEN = "Audit entries cannot be overwritten."

AT_TEXT = "2026-11-12 09:00:00"
AT = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)

# --- switching the application guard off and on again -------------------------------------------


def _guard_listeners():
    return [
        (Session, "do_orm_execute", audit_guard._guard_orm_execute),
        (Engine, "before_execute", audit_guard._guard_connection_execute),
        (AuditEntry, "before_update", models._refuse_audit_update),
        (AuditEntry, "before_delete", models._refuse_audit_delete),
    ]


@contextmanager
def guards_disabled():
    """Remove the application guard's listeners inside the block and restore them afterwards."""
    listeners = _guard_listeners()
    for target, name, function in listeners:
        assert event.contains(target, name, function), f"{name} guard is not registered"
    for target, name, function in listeners:
        event.remove(target, name, function)
    try:
        for target, name, function in listeners:
            assert not event.contains(target, name, function), f"{name} guard is still on"
        yield
    finally:
        for target, name, function in listeners:
            if not event.contains(target, name, function):
                event.listen(target, name, function)


@pytest.fixture
def guards_off():
    with guards_disabled():
        yield


@pytest.fixture
def seeded(migrated_engine):  # noqa: F811
    """A user, a case and one committed audit entry, on a migrated database."""
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn)
        entry_id = insert_audit_row(
            conn, case_id=case_id, actor_id=user_id, action="claimed", detail="original"
        )
    seeded = {
        "engine": migrated_engine,
        "user_id": user_id,
        "case_id": case_id,
        "entry_id": entry_id,
    }
    seeded["before"] = rows_now(migrated_engine)
    assert len(seeded["before"]) == 1
    return seeded


def rows_now(engine):
    """Every audit row as stored, read on a new connection."""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT id, case_id, actor_id, action, detail, at FROM audit_entries ORDER BY id"
        ).all()
    return [tuple(row) for row in rows]


def trigger_names(engine):
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'trigger'").all()
    return sorted(row[0] for row in rows)


def test_the_guard_listeners_are_restored_after_the_block(seeded):
    with guards_disabled():
        with seeded["engine"].connect() as conn:
            with pytest.raises(DBAPIError):  # the trigger, not the guard
                conn.execute(update(AUDIT).values(detail="changed"))
            conn.rollback()
    for target, name, function in _guard_listeners():
        assert event.contains(target, name, function)
    with seeded["engine"].connect() as conn:
        with pytest.raises(AuditImmutableError):  # the guard is back
            conn.execute(update(AUDIT).values(detail="changed"))
        conn.rollback()
    assert rows_now(seeded["engine"]) == seeded["before"]


def test_the_guard_listeners_are_restored_after_an_exception():
    with pytest.raises(RuntimeError):
        with guards_disabled():
            raise RuntimeError("boom")
    for target, name, function in _guard_listeners():
        assert event.contains(target, name, function)


def test_the_three_triggers_exist_on_the_migrated_database(seeded):
    assert trigger_names(seeded["engine"]) == [
        "audit_entries_no_delete",
        "audit_entries_no_reuse_id",
        "audit_entries_no_update",
    ]


# --- the routes ---------------------------------------------------------------------------------

# Raw SQL, once with `?` placeholders (exec_driver_sql, the DBAPI cursor) and once with names (text).
RAW_UPDATE = "UPDATE audit_entries SET detail = 'changed'"
RAW_DELETE = "DELETE FROM audit_entries"
_COLUMNS = "(id, case_id, actor_id, action, detail, at)"
RAW_REPLACE = (
    "INSERT OR REPLACE INTO audit_entries " + _COLUMNS + " VALUES "
    "(?, ?, ?, 'claimed', 'replaced', '" + AT_TEXT + "')"
)
RAW_REPLACE_BARE = (
    "REPLACE INTO audit_entries " + _COLUMNS + " VALUES "
    "(?, ?, ?, 'claimed', 'replaced', '" + AT_TEXT + "')"
)
RAW_UPSERT = (
    "INSERT INTO audit_entries " + _COLUMNS + " VALUES "
    "(?, ?, ?, 'claimed', 'upserted', '" + AT_TEXT + "') "
    "ON CONFLICT (id) DO UPDATE SET detail = 'upserted'"
)
TEXT_REPLACE = (
    "INSERT OR REPLACE INTO audit_entries " + _COLUMNS + " VALUES "
    "(:id, :case_id, :actor_id, 'claimed', 'replaced', '" + AT_TEXT + "')"
)
TEXT_UPSERT = (
    "INSERT INTO audit_entries " + _COLUMNS + " VALUES "
    "(:id, :case_id, :actor_id, 'claimed', 'upserted', '" + AT_TEXT + "') "
    "ON CONFLICT (id) DO UPDATE SET detail = 'upserted'"
)


def _args(seeded):
    return (seeded["entry_id"], seeded["case_id"], seeded["user_id"])


def _named(seeded):
    return {"id": seeded["entry_id"], "case_id": seeded["case_id"], "actor_id": seeded["user_id"]}


def _values(seeded, detail):
    """Typed values, for the table and the ORM entity (`at` is an aware datetime)."""
    return dict(
        id=seeded["entry_id"],
        case_id=seeded["case_id"],
        actor_id=seeded["user_id"],
        action="claimed",
        detail=detail,
        at=AT,
    )


def _plain_values(seeded, detail):
    """Untyped values, for a lightweight table (`at` is plain text)."""
    return {**_values(seeded, detail), "at": AT_TEXT}


def _light(name):
    """A lightweight `table(name, ...)`: a TableClause, not a Table."""
    return table(
        name,
        column("id"),
        column("case_id"),
        column("actor_id"),
        column("action"),
        column("detail"),
        column("at"),
    )


def _detached_entry(seeded):
    """An AuditEntry loaded in another session, which is then closed: a detached instance."""
    with Session(seeded["engine"]) as other:
        return other.get(AuditEntry, seeded["entry_id"])


def _upsert(target, values):
    return sqlite_insert(target).values(**values).on_conflict_do_update(
        index_elements=["id"], set_={"detail": "upserted"}
    )


def _from_select(prefix):
    return (
        insert(AUDIT)
        .prefix_with(*prefix)
        .from_select(
            ["id", "case_id", "actor_id", "action", "detail", "at"],
            select(
                AUDIT.c.id, AUDIT.c.case_id, AUDIT.c.actor_id, AUDIT.c.action, literal("replaced"), AUDIT.c.at
            ),
        )
    )


# Each route is (kind, message, function). The kind says what the function receives: a Session, a
# Connection or a stdlib sqlite3 cursor. Core statements are built from `seeded` so ids are real.
ROUTES = {}


def route(name, kind, message):
    def register(function):
        assert name not in ROUTES
        ROUTES[name] = (kind, message, function)
        return function

    return register


# ORM routes -------------------------------------------------------------------------------------


@route("orm_attribute_change_and_flush", "session", CHANGED)
def _orm_attribute_change(session, seeded):
    entry = session.get(AuditEntry, seeded["entry_id"])
    entry.detail = "changed"
    session.flush()


@route("orm_session_delete_and_flush", "session", REMOVED)
def _orm_session_delete(session, seeded):
    session.delete(session.get(AuditEntry, seeded["entry_id"]))
    session.flush()


@route("orm_merge_of_a_detached_change", "session", CHANGED)
def _orm_merge(session, seeded):
    entry = _detached_entry(seeded)
    entry.detail = "changed"
    session.merge(entry)
    session.flush()


@route("legacy_query_update", "session", CHANGED)
def _query_update(session, seeded):
    session.query(AuditEntry).update({"detail": "changed"})


@route("legacy_query_delete", "session", REMOVED)
def _query_delete(session, seeded):
    session.query(AuditEntry).delete()


@route("bulk_orm_update", "session", CHANGED)
def _bulk_update(session, seeded):
    session.execute(update(AuditEntry).values(detail="changed"))


@route("bulk_orm_delete", "session", REMOVED)
def _bulk_delete(session, seeded):
    session.execute(delete(AuditEntry))


@route("bulk_update_mappings", "session", CHANGED)
def _bulk_update_mappings(session, seeded):
    session.bulk_update_mappings(AuditEntry, [{"id": seeded["entry_id"], "detail": "changed"}])


@route("bulk_save_objects", "session", CHANGED)
def _bulk_save_objects(session, seeded):
    entry = _detached_entry(seeded)
    entry.detail = "changed"
    session.bulk_save_objects([entry])


@route("update_aliased_entity_session", "session", CHANGED)
def _update_aliased_session(session, seeded):
    session.execute(update(aliased(AuditEntry)).values(detail="changed"))


@route("delete_aliased_entity_session", "session", REMOVED)
def _delete_aliased_session(session, seeded):
    session.execute(delete(aliased(AuditEntry)))


@route("orm_upsert_session", "session", OVERWRITTEN)
def _orm_upsert_session(session, seeded):
    session.execute(_upsert(AuditEntry, _values(seeded, "upserted")))


# Core routes: the table, through the session and on a connection ------------------------------


@route("table_update_session", "session", CHANGED)
def _table_update_session(session, seeded):
    session.execute(update(AUDIT).values(detail="changed"))


@route("table_delete_session", "session", REMOVED)
def _table_delete_session(session, seeded):
    session.execute(delete(AUDIT))


@route("table_update_connection", "connection", CHANGED)
def _table_update_connection(conn, seeded):
    conn.execute(update(AUDIT).values(detail="changed"))


@route("table_delete_connection", "connection", REMOVED)
def _table_delete_connection(conn, seeded):
    conn.execute(delete(AUDIT))


# Aliases and a nested alias, through the session and on a connection ---------------------------

ALIASES = {
    "alias": lambda: AUDIT.alias("x"),
    "nested_alias": lambda: AUDIT.alias("x").alias("y"),
}

for _alias_name, _make_alias in ALIASES.items():
    for _kind in ("session", "connection"):

        def _update_alias(handle, seeded, _make_alias=_make_alias):
            handle.execute(update(_make_alias()).values(detail="changed"))

        def _delete_alias(handle, seeded, _make_alias=_make_alias):
            handle.execute(delete(_make_alias()))

        route(f"update_{_alias_name}_{_kind}", _kind, CHANGED)(_update_alias)
        route(f"delete_{_alias_name}_{_kind}", _kind, REMOVED)(_delete_alias)


# Case variants of the name, and a lightweight table ---------------------------------------------

for _spelling in ("audit_entries", "AUDIT_ENTRIES", "Audit_Entries"):
    for _kind in ("session", "connection"):

        def _update_light(handle, seeded, _spelling=_spelling):
            handle.execute(update(_light(_spelling)).values(detail="changed"))

        def _delete_light(handle, seeded, _spelling=_spelling):
            handle.execute(delete(_light(_spelling)))

        def _replace_light(handle, seeded, _spelling=_spelling):
            handle.execute(
                insert(_light(_spelling)).prefix_with("OR REPLACE").values(**_plain_values(seeded, "replaced"))
            )

        def _upsert_light(handle, seeded, _spelling=_spelling):
            handle.execute(_upsert(_light(_spelling), _plain_values(seeded, "upserted")))

        route(f"update_light_{_spelling}_{_kind}", _kind, CHANGED)(_update_light)
        route(f"delete_light_{_spelling}_{_kind}", _kind, REMOVED)(_delete_light)
        route(f"insert_or_replace_light_{_spelling}_{_kind}", _kind, OVERWRITTEN)(_replace_light)
        route(f"upsert_light_{_spelling}_{_kind}", _kind, OVERWRITTEN)(_upsert_light)


# lambda_stmt forms ------------------------------------------------------------------------------


def _lambda_update():
    return lambda_stmt(lambda: update(AUDIT).values(detail="changed"))


def _lambda_delete():
    return lambda_stmt(lambda: delete(AUDIT))


def _lambda_replace(seeded):
    entry_id, case_id, actor_id = _args(seeded)
    at_text = AT_TEXT
    return lambda_stmt(
        lambda: insert(AUDIT)
        .prefix_with("OR REPLACE")
        .values(
            id=entry_id,
            case_id=case_id,
            actor_id=actor_id,
            action="claimed",
            detail="replaced",
            at=at_text,
        )
    )


def _lambda_upsert(seeded):
    entry_id, case_id, actor_id = _args(seeded)
    at_text = AT_TEXT
    return lambda_stmt(
        lambda: sqlite_insert(AUDIT)
        .values(
            id=entry_id,
            case_id=case_id,
            actor_id=actor_id,
            action="claimed",
            detail="upserted",
            at=at_text,
        )
        .on_conflict_do_update(index_elements=["id"], set_={"detail": "upserted"})
    )


for _kind in ("session", "connection"):

    def _run_lambda_update(handle, seeded):
        handle.execute(_lambda_update())

    def _run_lambda_delete(handle, seeded):
        handle.execute(_lambda_delete())

    def _run_lambda_replace(handle, seeded):
        handle.execute(_lambda_replace(seeded))

    def _run_lambda_upsert(handle, seeded):
        handle.execute(_lambda_upsert(seeded))

    route(f"lambda_update_{_kind}", _kind, CHANGED)(_run_lambda_update)
    route(f"lambda_delete_{_kind}", _kind, REMOVED)(_run_lambda_delete)
    route(f"lambda_insert_or_replace_{_kind}", _kind, OVERWRITTEN)(_run_lambda_replace)
    route(f"lambda_on_conflict_do_update_{_kind}", _kind, OVERWRITTEN)(_run_lambda_upsert)


# INSERT OR REPLACE, REPLACE, from_select and upserts, through the session and on a connection ----

OVERWRITING = {
    "insert_or_replace": lambda seeded: insert(AUDIT)
    .prefix_with("OR REPLACE")
    .values(**_values(seeded, "replaced")),
    "insert_or_replace_two_prefixes": lambda seeded: insert(AUDIT)
    .prefix_with("OR")
    .prefix_with("REPLACE")
    .values(**_values(seeded, "replaced")),
    "insert_replace": lambda seeded: insert(AUDIT)
    .prefix_with("REPLACE")
    .values(**_values(seeded, "replaced")),
    "insert_or_replace_from_select": lambda seeded: _from_select(["OR REPLACE"]),
    "insert_or_replace_from_select_two_prefixes": lambda seeded: _from_select(["OR", "REPLACE"]),
    "sqlite_on_conflict_do_update": lambda seeded: _upsert(AUDIT, _values(seeded, "upserted")),
}

for _name, _build in OVERWRITING.items():
    for _kind in ("session", "connection"):

        def _run_overwrite(handle, seeded, _build=_build):
            handle.execute(_build(seeded))

        route(f"{_name}_{_kind}", _kind, OVERWRITTEN)(_run_overwrite)


# Raw SQL: text(), exec_driver_sql and a DBAPI cursor ---------------------------------------------


@route("text_update", "session", CHANGED)
def _text_update(session, seeded):
    session.execute(text(RAW_UPDATE))


@route("text_delete", "session", REMOVED)
def _text_delete(session, seeded):
    session.execute(text(RAW_DELETE))


@route("text_insert_or_replace", "session", OVERWRITTEN)
def _text_replace(session, seeded):
    session.execute(text(TEXT_REPLACE), _named(seeded))


@route("text_on_conflict_do_update", "session", OVERWRITTEN)
def _text_upsert(session, seeded):
    session.execute(text(TEXT_UPSERT), _named(seeded))


@route("text_update_connection", "connection", CHANGED)
def _text_update_connection(conn, seeded):
    conn.execute(text(RAW_UPDATE))


@route("exec_driver_sql_update", "connection", CHANGED)
def _driver_update(conn, seeded):
    conn.exec_driver_sql(RAW_UPDATE)


@route("exec_driver_sql_delete", "connection", REMOVED)
def _driver_delete(conn, seeded):
    conn.exec_driver_sql(RAW_DELETE)


@route("exec_driver_sql_insert_or_replace", "connection", OVERWRITTEN)
def _driver_replace(conn, seeded):
    conn.exec_driver_sql(RAW_REPLACE, _args(seeded))


@route("exec_driver_sql_replace", "connection", OVERWRITTEN)
def _driver_replace_bare(conn, seeded):
    conn.exec_driver_sql(RAW_REPLACE_BARE, _args(seeded))


@route("exec_driver_sql_on_conflict_do_update", "connection", OVERWRITTEN)
def _driver_upsert(conn, seeded):
    conn.exec_driver_sql(RAW_UPSERT, _args(seeded))


@route("dbapi_cursor_update", "dbapi", CHANGED)
def _cursor_update(cursor, seeded):
    cursor.execute(RAW_UPDATE)


@route("dbapi_cursor_delete", "dbapi", REMOVED)
def _cursor_delete(cursor, seeded):
    cursor.execute(RAW_DELETE)


@route("dbapi_cursor_insert_or_replace", "dbapi", OVERWRITTEN)
def _cursor_replace(cursor, seeded):
    cursor.execute(RAW_REPLACE, _args(seeded))


@route("dbapi_cursor_replace", "dbapi", OVERWRITTEN)
def _cursor_replace_bare(cursor, seeded):
    cursor.execute(RAW_REPLACE_BARE, _args(seeded))


@route("dbapi_cursor_on_conflict_do_update", "dbapi", OVERWRITTEN)
def _cursor_upsert(cursor, seeded):
    cursor.execute(RAW_UPSERT, _args(seeded))


def attempt(seeded, kind, function):
    """Run one route. Return the database's own error; roll back whatever the route did."""
    engine = seeded["engine"]
    if kind == "session":
        with Session(engine) as session:
            with pytest.raises(DBAPIError) as info:
                function(session, seeded)
            session.rollback()
        return info.value.orig
    if kind == "connection":
        with engine.connect() as conn:
            with pytest.raises(DBAPIError) as info:
                function(conn, seeded)
            conn.rollback()
        return info.value.orig
    assert kind == "dbapi"
    # A stdlib sqlite3 connection to the same database file, with no SQLAlchemy in between.
    raw = sqlite3.connect(engine.url.database)
    try:
        with pytest.raises(sqlite3.DatabaseError) as info:
            function(raw.cursor(), seeded)
        raw.rollback()
    finally:
        raw.close()
    return info.value


@pytest.mark.parametrize("name", sorted(ROUTES))
def test_the_database_refuses_the_route_with_the_trigger_message(seeded, guards_off, name):
    kind, message, function = ROUTES[name]
    error = attempt(seeded, kind, function)
    assert isinstance(error, sqlite3.IntegrityError)  # RAISE(ABORT) is a constraint error
    assert not isinstance(error, AuditImmutableError)
    assert str(error) == message  # the fixed text: nothing else, no id, no detail
    assert rows_now(seeded["engine"]) == seeded["before"]


def test_the_routes_cover_every_kind_the_issue_lists():
    # A guard against someone deleting a route: the families below must each have an entry.
    families = [
        "orm_attribute_change", "orm_session_delete", "orm_merge", "legacy_query_update",
        "legacy_query_delete", "bulk_orm_update", "bulk_orm_delete", "bulk_update_mappings",
        "bulk_save_objects", "table_update_session", "table_delete_session",
        "table_update_connection", "table_delete_connection", "update_alias_",
        "update_nested_alias_", "update_aliased_entity", "update_light_AUDIT_ENTRIES",
        "update_light_Audit_Entries", "lambda_update_", "lambda_delete_",
        "lambda_insert_or_replace_", "lambda_on_conflict_do_update_", "insert_or_replace_session",
        "insert_or_replace_two_prefixes_", "insert_or_replace_from_select_",
        "sqlite_on_conflict_do_update_", "text_update", "exec_driver_sql_update", "dbapi_cursor_update",
    ]  # fmt: skip
    for family in families:
        assert any(name.startswith(family) for name in ROUTES), family


# --- PRAGMA recursive_triggers does not change the result ---------------------------------------

RECURSIVE = {
    "off": ("PRAGMA recursive_triggers = OFF", 0),
    "on": ("PRAGMA recursive_triggers = ON", 1),
}

OVERWRITE_CASES = {
    "insert_or_replace": lambda seeded: insert(AUDIT).prefix_with("OR REPLACE").values(**_values(seeded, "replaced")),
    "replace": lambda seeded: insert(AUDIT).prefix_with("REPLACE").values(**_values(seeded, "replaced")),
    "insert_or_replace_from_select": lambda seeded: _from_select(["OR REPLACE"]),
    "on_conflict_do_update": lambda seeded: _upsert(AUDIT, _values(seeded, "upserted")),
}


@pytest.mark.parametrize("case", sorted(OVERWRITE_CASES))
def test_recursive_triggers_off_and_on_give_the_same_result(seeded, guards_off, case):
    results = {}
    for setting, (pragma, expected) in RECURSIVE.items():
        with seeded["engine"].connect() as conn:
            try:
                conn.exec_driver_sql(pragma)
                assert conn.exec_driver_sql("PRAGMA recursive_triggers").scalar() == expected
                with pytest.raises(DBAPIError) as info:
                    conn.execute(OVERWRITE_CASES[case](seeded))
                conn.rollback()
            finally:
                conn.exec_driver_sql("PRAGMA recursive_triggers = OFF")  # the default again
                conn.rollback()
        results[setting] = (type(info.value.orig).__name__, str(info.value.orig))
        assert rows_now(seeded["engine"]) == seeded["before"], setting
    assert results["off"] == results["on"]
    assert results["off"] == ("IntegrityError", OVERWRITTEN)


def test_recursive_triggers_on_still_refuses_an_update_and_a_delete(seeded, guards_off):
    for statement, message in ((update(AUDIT).values(detail="changed"), CHANGED), (delete(AUDIT), REMOVED)):
        with seeded["engine"].connect() as conn:
            try:
                conn.exec_driver_sql("PRAGMA recursive_triggers = ON")
                with pytest.raises(DBAPIError) as info:
                    conn.execute(statement)
                conn.rollback()
            finally:
                conn.exec_driver_sql("PRAGMA recursive_triggers = OFF")
                conn.rollback()
        assert str(info.value.orig) == message
    assert rows_now(seeded["engine"]) == seeded["before"]


# --- what still works ---------------------------------------------------------------------------


def test_a_plain_insert_with_a_new_id_succeeds(seeded, guards_off):
    with seeded["engine"].begin() as conn:
        conn.execute(insert(AUDIT).values(**{**_values(seeded, "plain"), "id": 50}))
    ids = [row[0] for row in rows_now(seeded["engine"])]
    assert ids == [seeded["entry_id"], 50]


def test_an_insert_with_no_id_or_a_null_id_still_autoincrements(seeded, guards_off):
    engine = seeded["engine"]
    values = {key: value for key, value in _values(seeded, "auto").items() if key != "id"}
    with engine.begin() as conn:
        first = conn.execute(insert(AUDIT).values(**values)).inserted_primary_key[0]
        conn.exec_driver_sql(
            "INSERT INTO audit_entries (id, case_id, actor_id, action, detail, at) "
            "VALUES (NULL, ?, ?, 'claimed', 'null id', '" + AT_TEXT + "')",
            (seeded["case_id"], seeded["user_id"]),
        )
    ids = [row[0] for row in rows_now(engine)]
    assert ids[0] == seeded["entry_id"]
    assert ids[1] == first
    assert ids[2] > first
    assert len(set(ids)) == 3


def test_insert_or_replace_and_an_upsert_with_a_new_id_succeed(seeded, guards_off):
    with seeded["engine"].begin() as conn:
        conn.execute(insert(AUDIT).prefix_with("OR REPLACE").values(**{**_values(seeded, "new1"), "id": 60}))
        conn.execute(_upsert(AUDIT, {**_values(seeded, "new2"), "id": 61}))
        conn.execute(
            sqlite_insert(AUDIT).values(**{**_values(seeded, "new3"), "id": 62}).on_conflict_do_nothing()
        )
    assert [row[0] for row in rows_now(seeded["engine"])] == [seeded["entry_id"], 60, 61, 62]


def test_append_audit_succeeds_on_the_migrated_database(seeded, guards_off):
    with Session(seeded["engine"]) as session:
        first = append_audit(
            session, case_id=seeded["case_id"], actor_id=seeded["user_id"], action="note_added", detail="n"
        )
        second = append_audit(session, case_id=seeded["case_id"], actor_id=None, action="ingested", detail="m")
        session.commit()
        ids = (first.id, second.id)
    assert ids[0] > seeded["entry_id"]
    assert ids[1] > ids[0]


def test_an_id_is_not_reused_after_the_database_refuses_a_delete(seeded, guards_off):
    # The row cannot be deleted, so the highest id stays, and the next insert goes above it.
    error = attempt(seeded, "connection", ROUTES["table_delete_connection"][2])
    assert str(error) == REMOVED
    with seeded["engine"].begin() as conn:
        again = insert_audit_row(conn, case_id=seeded["case_id"], actor_id=seeded["user_id"])
    assert again > seeded["entry_id"]


def test_the_refusal_message_holds_no_value(seeded, guards_off):
    marker = "marker-4f9c1e"
    with seeded["engine"].begin() as conn:
        entry_id = insert_audit_row(
            conn, case_id=seeded["case_id"], actor_id=seeded["user_id"], detail=marker
        )
    with seeded["engine"].connect() as conn:
        with pytest.raises(DBAPIError) as info:
            conn.execute(update(AUDIT).where(AUDIT.c.id == entry_id).values(detail="x"))
        conn.rollback()
    text_of_error = "".join(str(part) for part in (info.value, info.value.orig))
    assert marker not in text_of_error
    assert str(entry_id) not in str(info.value.orig)
