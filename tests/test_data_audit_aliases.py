"""Alias and case-variant routes to `audit_entries` are refused, and `allow_schema_changes` is pinned (#11)."""

import ast
from datetime import UTC, datetime
from pathlib import Path

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
    DDL,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    column,
    delete,
    insert,
    select,
    table,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased
from sqlalchemy.schema import DropTable

from socboard import audit_guard
from socboard.audit import append_audit
from socboard.audit_guard import AuditImmutableError, allow_schema_changes, refused_statement
from socboard.models import AuditEntry

PACKAGE = Path(__file__).resolve().parents[1] / "socboard"
AUDIT = AuditEntry.__table__


@pytest.fixture
def seeded(migrated_engine):  # noqa: F811
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn)
        entry_id = insert_audit_row(
            conn, case_id=case_id, actor_id=user_id, action="claimed", detail="original"
        )
    return {"engine": migrated_engine, "user_id": user_id, "case_id": case_id, "entry_id": entry_id}


def _unchanged(seeded):
    with Session(seeded["engine"]) as session:
        rows = session.execute(select(AuditEntry.id, AuditEntry.detail, AuditEntry.action)).all()
    return [tuple(row) for row in rows] == [(seeded["entry_id"], "original", "claimed")]


def _other_case_table(name):
    return Table(
        name,
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("case_id", Integer),
        Column("actor_id", Integer),
        Column("action", String),
        Column("detail", String),
        Column("at", DateTime),
    )


def _lightweight(name):
    return table(name, column("id"), column("detail"))


def _values(seeded, detail):
    return dict(
        id=seeded["entry_id"],
        case_id=seeded["case_id"],
        actor_id=seeded["user_id"],
        action="claimed",
        detail=detail,
        at=datetime(2026, 11, 12, 9, 0, tzinfo=UTC),
    )


def _plain_values(seeded, detail):
    values = _values(seeded, detail)
    values["at"] = "2026-11-12 09:00:00"
    return values


STATEMENTS = {
    "update_alias": lambda s: update(AUDIT.alias("x")).values(detail="changed"),
    "delete_alias": lambda s: delete(AUDIT.alias("x")),
    "update_nested_alias": lambda s: update(AUDIT.alias("x").alias("y")).values(detail="changed"),
    "delete_nested_alias": lambda s: delete(AUDIT.alias("x").alias("y")),
    "update_aliased_entity": lambda s: update(aliased(AuditEntry)).values(detail="changed"),
    "update_upper_case_lightweight_table": lambda s: update(_lightweight("AUDIT_ENTRIES")).values(
        detail="changed"
    ),
    "delete_upper_case_lightweight_table": lambda s: delete(_lightweight("AUDIT_ENTRIES")),
    "update_mixed_case_table": lambda s: update(_other_case_table("Audit_Entries")).values(
        detail="changed"
    ),
    "delete_mixed_case_table": lambda s: delete(_other_case_table("Audit_Entries")),
    "insert_or_replace_mixed_case_table": lambda s: insert(_other_case_table("Audit_Entries"))
    .prefix_with("OR REPLACE")
    .values(**_plain_values(s, "replaced")),
    "upsert_mixed_case_table": lambda s: sqlite_insert(_other_case_table("Audit_Entries"))
    .values(**_plain_values(s, "upserted"))
    .on_conflict_do_update(index_elements=["id"], set_={"detail": "upserted"}),
    "drop_table_mixed_case": lambda s: DropTable(_other_case_table("Audit_Entries")),
    "drop_table_upper_case": lambda s: DropTable(_other_case_table("AUDIT_ENTRIES")),
}


@pytest.mark.parametrize("name", STATEMENTS)
def test_the_route_is_refused_by_the_guard_function(seeded, name):
    assert refused_statement(STATEMENTS[name](seeded))


@pytest.mark.parametrize("name", STATEMENTS)
def test_the_route_on_a_connection_is_refused_and_the_row_is_unchanged(seeded, name):
    statement = STATEMENTS[name](seeded)
    with seeded["engine"].connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(statement)
        conn.rollback()
    assert _unchanged(seeded)


@pytest.mark.parametrize("name", STATEMENTS)
def test_the_route_through_a_session_is_refused_and_the_row_is_unchanged(seeded, name):
    statement = STATEMENTS[name](seeded)
    with Session(seeded["engine"]) as session:
        with pytest.raises(AuditImmutableError):
            session.execute(statement)
        session.rollback()
    assert _unchanged(seeded)


def test_the_named_alias_update_and_delete_are_refused_on_a_connection_and_a_session(seeded):
    for build in (
        lambda: update(AUDIT.alias("x")).values(detail="FORGED"),
        lambda: delete(AUDIT.alias("x")),
    ):
        with seeded["engine"].connect() as conn:
            with pytest.raises(AuditImmutableError):
                conn.execute(build())
            conn.rollback()
        with Session(seeded["engine"]) as session:
            with pytest.raises(AuditImmutableError):
                session.execute(build())
            session.rollback()
        assert _unchanged(seeded)


def test_ddl_text_in_upper_case_is_refused(seeded):
    # DDL("...") text is matched as a whole word, case-insensitively.
    for sql in ('DROP TABLE "AUDIT_ENTRIES"', "ALTER TABLE Audit_Entries ADD COLUMN extra TEXT"):
        with seeded["engine"].connect() as conn:
            with pytest.raises(AuditImmutableError):
                conn.execute(DDL(sql))
            conn.rollback()
        assert _unchanged(seeded)


def test_alembic_style_ddl_on_an_upper_case_name_is_refused(seeded):
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    with seeded["engine"].connect() as conn:
        operations = Operations(MigrationContext.configure(conn))
        with pytest.raises(AuditImmutableError):
            operations.drop_table("AUDIT_ENTRIES")
        with pytest.raises(AuditImmutableError):
            operations.rename_table("Audit_Entries", "gone")
        conn.rollback()
    assert _unchanged(seeded)


def test_a_plain_insert_and_append_audit_still_pass(seeded):
    with seeded["engine"].begin() as conn:
        plain = {key: value for key, value in _values(seeded, "plain").items() if key != "id"}
        conn.execute(insert(AUDIT).values(**plain))  # no explicit id: the guard refuses one (#99)
    with Session(seeded["engine"]) as session:
        append_audit(
            session, case_id=seeded["case_id"], actor_id=seeded["user_id"], action="note_added", detail="n"
        )
        session.commit()
    with seeded["engine"].connect() as conn:
        details = conn.execute(select(AuditEntry.detail).order_by(AuditEntry.id)).scalars().all()
    assert details == ["original", "plain", "n"]


def test_a_table_with_another_name_behind_an_alias_is_not_blocked(seeded):
    other = _other_case_table("scratch_rows").alias("s")
    assert not refused_statement(update(other).values(detail="x"))
    assert not refused_statement(delete(other))


# --- allow_schema_changes -----------------------------------------------------------------------


def test_allow_schema_changes_restores_the_guard_on_exit(seeded):
    statement = DropTable(_other_case_table("audit_entries"))
    assert refused_statement(statement)
    with allow_schema_changes():
        assert not refused_statement(statement)
        assert refused_statement(update(AUDIT).values(detail="x"))  # DML stays refused inside
    assert refused_statement(statement)
    assert _unchanged(seeded)


def test_allow_schema_changes_restores_the_guard_after_an_exception():
    statement = DropTable(_other_case_table("audit_entries"))
    with pytest.raises(RuntimeError):
        with allow_schema_changes():
            assert not refused_statement(statement)
            raise RuntimeError("boom")
    assert refused_statement(statement)
    assert audit_guard._schema_changes_allowed.get() is False


def test_allow_schema_changes_restores_the_guard_when_nested():
    statement = DropTable(_other_case_table("audit_entries"))
    with allow_schema_changes():
        with allow_schema_changes():
            pass
        assert not refused_statement(statement)
    assert refused_statement(statement)


# --- only migrate.py calls allow_schema_changes ---------------------------------------------------


def _calls_allow_schema_changes(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "allow_schema_changes":
                yield node
            if isinstance(func, ast.Attribute) and func.attr == "allow_schema_changes":
                yield node


def test_only_migrate_py_calls_allow_schema_changes():
    callers = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name == "audit_guard.py":  # the definition
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(True for _ in _calls_allow_schema_changes(tree)):
            callers.append(path.relative_to(PACKAGE).as_posix())
    assert callers == ["migrate.py"]


def test_the_caller_check_would_notice_another_caller():
    for source in (
        "from socboard.audit_guard import allow_schema_changes\nwith allow_schema_changes():\n    pass\n",
        "import socboard.audit_guard as g\nwith g.allow_schema_changes():\n    pass\n",
    ):
        assert list(_calls_allow_schema_changes(ast.parse(source)))
