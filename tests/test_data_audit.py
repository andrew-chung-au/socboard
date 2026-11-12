"""The audit function and the append-only guards (#11)."""

import ast
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from data_support import (
    insert_alert,
    insert_audit_row,
    insert_case,
    insert_user,
    isolated_database_env,  # noqa: F401
    migrated_engine,  # noqa: F401
    no_default_database_file,  # noqa: F401
)
from sqlalchemy import DDL, delete, event, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable, DropTable

from socboard.audit import append_audit
from socboard.audit_guard import AuditImmutableError
from socboard.domain import AUDIT_ACTIONS
from socboard.models import AuditEntry, Case

PACKAGE = Path(__file__).resolve().parents[1] / "socboard"


@pytest.fixture
def seeded(migrated_engine):
    """A user, a case and one existing audit entry, committed."""
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn)
        entry_id = insert_audit_row(
            conn, case_id=case_id, actor_id=user_id, action="claimed", detail="original"
        )
    return {"engine": migrated_engine, "user_id": user_id, "case_id": case_id, "entry_id": entry_id}


# --- append_audit -----------------------------------------------------------------------------


def test_append_audit_adds_flushes_and_does_not_commit(seeded):
    engine = seeded["engine"]
    with Session(engine) as session:
        entry = append_audit(
            session,
            case_id=seeded["case_id"],
            actor_id=seeded["user_id"],
            action="note_added",
            detail="a note",
        )
        assert entry.id is not None  # flushed
        assert entry in session
        session.rollback()  # not committed, so the entry disappears
    with engine.connect() as conn:
        actions = conn.execute(select(AuditEntry.action)).scalars().all()
    assert actions == ["claimed"]


def test_append_audit_is_visible_after_the_caller_commits(seeded):
    engine = seeded["engine"]
    with Session(engine) as session:
        append_audit(
            session, case_id=seeded["case_id"], actor_id=None, action="ingested", detail="from siem"
        )
        session.commit()
    with engine.connect() as conn:
        rows = conn.execute(select(AuditEntry.action, AuditEntry.actor_id).order_by(AuditEntry.id)).all()
    assert [tuple(row) for row in rows] == [("claimed", seeded["user_id"]), ("ingested", None)]


def test_append_audit_defaults_at_to_now_in_utc(seeded):
    before = datetime.now(UTC)
    with Session(seeded["engine"]) as session:
        entry = append_audit(
            session, case_id=seeded["case_id"], actor_id=seeded["user_id"], action="claimed", detail="d"
        )
        session.commit()
        stored = entry.at
    after = datetime.now(UTC)
    assert stored.utcoffset() == timedelta(0)
    assert before - timedelta(seconds=1) <= stored <= after + timedelta(seconds=1)


def test_append_audit_stores_an_explicit_time_as_the_same_instant(seeded):
    at = datetime(2026, 11, 12, 19, 0, tzinfo=timezone(timedelta(hours=10)))
    with Session(seeded["engine"]) as session:
        entry = append_audit(
            session, case_id=seeded["case_id"], actor_id=seeded["user_id"], action="claimed", detail="d", at=at
        )
        entry_id = entry.id
        session.commit()
    with Session(seeded["engine"]) as session:
        stored = session.get(AuditEntry, entry_id).at
    assert stored == at
    assert stored.utcoffset() == timedelta(0)


def test_an_unknown_action_raises_before_the_database_is_touched(seeded):
    with Session(seeded["engine"]) as session:
        statements = []
        event.listen(seeded["engine"], "before_cursor_execute", lambda *args: statements.append(args[2]))
        with pytest.raises(ValueError):
            append_audit(
                session, case_id=seeded["case_id"], actor_id=seeded["user_id"], action="deleted", detail="d"
            )
        assert statements == []
        assert not session.new


@pytest.mark.parametrize("action", AUDIT_ACTIONS)
def test_every_allowed_action_is_accepted_by_the_function(seeded, action):
    with Session(seeded["engine"]) as session:
        append_audit(
            session, case_id=seeded["case_id"], actor_id=seeded["user_id"], action=action, detail="d"
        )
        session.commit()


def test_a_null_actor_for_a_claim_is_refused_by_the_database(seeded):
    with Session(seeded["engine"]) as session:
        with pytest.raises(IntegrityError):
            append_audit(session, case_id=seeded["case_id"], actor_id=None, action="claimed", detail="d")
        session.rollback()


def test_a_naive_time_is_refused(seeded):
    with Session(seeded["engine"]) as session:
        with pytest.raises(StatementError):
            append_audit(
                session,
                case_id=seeded["case_id"],
                actor_id=seeded["user_id"],
                action="claimed",
                detail="d",
                at=datetime(2026, 11, 12, 9, 0),
            )
        session.rollback()


# --- entries read back in insertion order, ids are never reused --------------------------------


def test_entries_read_back_in_insertion_order_even_when_the_times_tie_or_run_backwards(seeded):
    same = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    with Session(seeded["engine"]) as session:
        for index, at in enumerate([same, same, same - timedelta(hours=3), same]):
            append_audit(
                session,
                case_id=seeded["case_id"],
                actor_id=seeded["user_id"],
                action="note_added",
                detail=f"entry-{index}",
                at=at,
            )
        session.commit()
    with seeded["engine"].connect() as conn:
        details = conn.execute(
            select(AuditEntry.detail).where(AuditEntry.case_id == seeded["case_id"]).order_by(AuditEntry.id)
        ).scalars().all()
    assert details == ["original", "entry-0", "entry-1", "entry-2", "entry-3"]


def test_an_id_is_never_reused(seeded):
    # Raw SQL through text() is not blocked (see the decision record); this is the only way in a
    # test to remove a row, and it shows the table does not reuse the highest id.
    engine = seeded["engine"]
    with engine.begin() as conn:
        last = insert_audit_row(conn, case_id=seeded["case_id"], actor_id=seeded["user_id"])
        conn.execute(text("DELETE FROM audit_entries WHERE id = :id"), {"id": last})
        again = insert_audit_row(conn, case_id=seeded["case_id"], actor_id=seeded["user_id"])
    assert again > last


def test_the_audit_table_uses_autoincrement(seeded):
    with seeded["engine"].connect() as conn:
        sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name = 'audit_entries'").scalar()
    assert "AUTOINCREMENT" in sql.upper()


# --- entries can be neither changed nor removed -------------------------------------------------


def _unchanged(seeded):
    with Session(seeded["engine"]) as session:
        rows = session.execute(select(AuditEntry.id, AuditEntry.detail, AuditEntry.action)).all()
    return [tuple(row) for row in rows] == [(seeded["entry_id"], "original", "claimed")]


def test_changing_an_attribute_and_flushing_is_refused(seeded):
    with Session(seeded["engine"]) as session:
        entry = session.get(AuditEntry, seeded["entry_id"])
        entry.detail = "changed"
        with pytest.raises(AuditImmutableError):
            session.flush()
        session.rollback()
    assert _unchanged(seeded)


def test_deleting_through_the_session_is_refused(seeded):
    with Session(seeded["engine"]) as session:
        entry = session.get(AuditEntry, seeded["entry_id"])
        session.delete(entry)
        with pytest.raises(AuditImmutableError):
            session.flush()
        session.rollback()
    assert _unchanged(seeded)


def test_a_bulk_orm_update_is_refused(seeded):
    with Session(seeded["engine"]) as session:
        with pytest.raises(AuditImmutableError):
            session.execute(update(AuditEntry).values(detail="changed"))
        session.rollback()
    assert _unchanged(seeded)


def test_a_bulk_orm_delete_is_refused(seeded):
    with Session(seeded["engine"]) as session:
        with pytest.raises(AuditImmutableError):
            session.execute(delete(AuditEntry))
        session.rollback()
    assert _unchanged(seeded)


def test_a_core_update_of_the_table_through_the_session_is_refused(seeded):
    with Session(seeded["engine"]) as session:
        with pytest.raises(AuditImmutableError):
            session.execute(update(AuditEntry.__table__).values(detail="changed"))
        session.rollback()
    assert _unchanged(seeded)


def test_a_core_delete_of_the_table_through_the_session_is_refused(seeded):
    with Session(seeded["engine"]) as session:
        with pytest.raises(AuditImmutableError):
            session.execute(delete(AuditEntry.__table__))
        session.rollback()
    assert _unchanged(seeded)


def test_bulk_update_mappings_is_refused(seeded):
    with Session(seeded["engine"]) as session:
        with pytest.raises(AuditImmutableError):
            session.bulk_update_mappings(AuditEntry, [{"id": seeded["entry_id"], "detail": "changed"}])
        session.rollback()
    assert _unchanged(seeded)


def test_a_core_update_on_a_connection_is_refused(seeded):
    with seeded["engine"].connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(update(AuditEntry.__table__).values(detail="changed"))
        conn.rollback()
    assert _unchanged(seeded)


def test_a_core_delete_on_a_connection_is_refused(seeded):
    with seeded["engine"].connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(delete(AuditEntry.__table__))
        conn.rollback()
    assert _unchanged(seeded)


def test_updating_another_table_is_not_blocked(seeded):
    with seeded["engine"].begin() as conn:
        conn.execute(update(Case.__table__).values(status="Investigating"))


# --- overwriting by INSERT, and DDL on the table (the gap QA found in #11) ----------------------

AUDIT = AuditEntry.__table__


def _entry_values(seeded, detail):
    return dict(
        id=seeded["entry_id"],
        case_id=seeded["case_id"],
        actor_id=seeded["user_id"],
        action="claimed",
        detail=detail,
        at=datetime(2026, 11, 12, 9, 0, tzinfo=UTC),
    )


def _insert_or_replace(seeded):
    return insert(AUDIT).prefix_with("OR REPLACE").values(**_entry_values(seeded, "replaced"))


def _insert_replace_prefix(seeded):
    return insert(AUDIT).prefix_with("REPLACE").values(**_entry_values(seeded, "replaced"))


def _sqlite_upsert(seeded):
    return (
        sqlite_insert(AUDIT)
        .values(**_entry_values(seeded, "upserted"))
        .on_conflict_do_update(index_elements=["id"], set_={"detail": "upserted"})
    )


def _postgresql_upsert(seeded):
    return (
        postgresql_insert(AUDIT)
        .values(**_entry_values(seeded, "upserted"))
        .on_conflict_do_update(index_elements=["id"], set_={"detail": "upserted"})
    )


def _orm_sqlite_upsert(seeded):
    return (
        sqlite_insert(AuditEntry)
        .values(**_entry_values(seeded, "upserted"))
        .on_conflict_do_update(index_elements=["id"], set_={"detail": "upserted"})
    )


OVERWRITING_INSERTS = {
    "insert_or_replace": _insert_or_replace,
    "insert_replace_prefix": _insert_replace_prefix,
    "sqlite_on_conflict_do_update": _sqlite_upsert,
    "postgresql_on_conflict_do_update": _postgresql_upsert,
    "orm_sqlite_on_conflict_do_update": _orm_sqlite_upsert,
}

DDL_STATEMENTS = {
    "drop_table_construct": lambda: DropTable(AUDIT),
    "create_table_construct": lambda: CreateTable(AUDIT),
    "ddl_text_drop_table": lambda: DDL("DROP TABLE audit_entries"),
    "ddl_text_alter_table": lambda: DDL("ALTER TABLE audit_entries ADD COLUMN extra TEXT"),
    "ddl_against_the_table": lambda: DDL("DROP TABLE %(table)s").against(AUDIT),
}


@pytest.mark.parametrize("name", OVERWRITING_INSERTS)
def test_an_overwriting_insert_on_a_connection_is_refused(seeded, name):
    statement = OVERWRITING_INSERTS[name](seeded)
    with seeded["engine"].connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(statement)
        conn.rollback()
    assert _unchanged(seeded)


@pytest.mark.parametrize("name", OVERWRITING_INSERTS)
def test_an_overwriting_insert_through_the_session_is_refused(seeded, name):
    statement = OVERWRITING_INSERTS[name](seeded)
    with Session(seeded["engine"]) as session:
        with pytest.raises(AuditImmutableError):
            session.execute(statement)
        session.rollback()
    assert _unchanged(seeded)


@pytest.mark.parametrize("name", DDL_STATEMENTS)
def test_ddl_on_the_audit_table_on_a_connection_is_refused(seeded, name):
    statement = DDL_STATEMENTS[name]()
    with seeded["engine"].connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(statement)
        conn.rollback()
    assert _unchanged(seeded)


@pytest.mark.parametrize("name", DDL_STATEMENTS)
def test_ddl_on_the_audit_table_through_the_session_is_refused(seeded, name):
    statement = DDL_STATEMENTS[name]()
    with Session(seeded["engine"]) as session:
        with pytest.raises(AuditImmutableError):
            session.execute(statement)
        session.rollback()
    assert _unchanged(seeded)


def test_dropping_the_table_with_the_metadata_helper_is_refused(seeded):
    with pytest.raises(AuditImmutableError):
        AUDIT.drop(seeded["engine"])
    assert _unchanged(seeded)


def test_a_plain_insert_and_append_audit_still_work(seeded):
    engine = seeded["engine"]
    with engine.begin() as conn:
        conn.execute(insert(AUDIT).values(**{**_entry_values(seeded, "plain"), "id": 50}))
        conn.execute(
            sqlite_insert(AUDIT)
            .values(**{**_entry_values(seeded, "ignored"), "id": 51})
            .on_conflict_do_nothing()
        )
    with Session(engine) as session:
        append_audit(
            session, case_id=seeded["case_id"], actor_id=seeded["user_id"], action="note_added", detail="n"
        )
        session.commit()
    with engine.connect() as conn:
        details = conn.execute(select(AuditEntry.detail).order_by(AuditEntry.id)).scalars().all()
    assert details == ["original", "plain", "ignored", "n"]  # ids 1, 50, 51, then 52


def test_ddl_on_another_table_is_not_blocked(seeded):
    with seeded["engine"].begin() as conn:
        conn.execute(DDL("CREATE TABLE scratch_table (id INTEGER)"))
        conn.execute(DDL("DROP TABLE scratch_table"))


def test_raw_sql_is_not_blocked_which_is_the_exemption_the_record_names(seeded):
    # exec_driver_sql and text() are raw SQL, which the decision record leaves outside the guard.
    # If a later change blocks them, this test fails so the record and the module docstring can be
    # revisited together with the code.
    engine = seeded["engine"]
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE audit_entries SET detail = 'raw'")
    with engine.connect() as conn:
        assert conn.execute(select(AuditEntry.detail)).scalars().all() == ["raw"]
    with engine.begin() as conn:
        conn.execute(text("UPDATE audit_entries SET detail = 'text'"))
    with engine.connect() as conn:
        assert conn.execute(select(AuditEntry.detail)).scalars().all() == ["text"]


# --- only audit.py constructs an AuditEntry ----------------------------------------------------


def _python_files():
    return sorted(path for path in PACKAGE.rglob("*.py"))


def _constructs_audit_entry(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "AuditEntry":
                yield node
            if isinstance(func, ast.Attribute) and func.attr == "AuditEntry":
                yield node


def test_only_audit_py_constructs_an_audit_entry():
    callers = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(True for _ in _constructs_audit_entry(tree)):
            callers.append(path.relative_to(PACKAGE).as_posix())
    assert callers == ["audit.py"]


def test_the_construction_check_would_notice_another_caller():
    tree = ast.parse("from socboard.models import AuditEntry\nentry = AuditEntry(case_id=1)\n")
    assert list(_constructs_audit_entry(tree))
    tree = ast.parse("import socboard.models as m\nentry = m.AuditEntry()\n")
    assert list(_constructs_audit_entry(tree))


# --- the data layer raises no HTTP errors, and builds no query by string formatting -------------

QUERY_CALLS = {"text", "execute", "exec_driver_sql", "executemany"}


def _formatted(node):
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return any(
            isinstance(side, ast.JoinedStr)
            or (isinstance(side, ast.Constant) and isinstance(side.value, str))
            for side in (node.left, node.right)
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return True
    return False


def _query_calls_with_formatting(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in QUERY_CALLS and _formatted(node.args[0]):
            yield node


def test_no_query_is_built_by_string_formatting():
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders += [f"{path.name}:{node.lineno}" for node in _query_calls_with_formatting(tree)]
    assert offenders == []


def test_the_formatting_check_would_notice_a_formatted_query():
    for source in (
        'conn.execute(text(f"SELECT {x}"))\ntext(f"SELECT {x}")',
        'conn.exec_driver_sql("SELECT " + x)',
        'text("SELECT %s" % x)',
        'conn.execute("SELECT {}".format(x))',
    ):
        assert list(_query_calls_with_formatting(ast.parse(source))), source
    assert not list(_query_calls_with_formatting(ast.parse('conn.exec_driver_sql("PRAGMA foreign_keys")')))


def test_no_data_layer_code_raises_an_api_http_exception():
    data_layer = [
        path
        for path in _python_files()
        if path.name not in {"app.py", "errors.py", "server.py", "__init__.py"}
    ]
    assert data_layer
    for path in data_layer:
        assert "ApiHTTPException" not in path.read_text(encoding="utf-8"), path.name
