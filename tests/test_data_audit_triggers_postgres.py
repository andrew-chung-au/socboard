"""The append-only audit trail on Postgres: migration 0002's four triggers and the two roles (#193, D6a).

Postgres only (`postgres_only`): deselected in the default SQLite run, run by the Postgres test job.
The SQLite triggers and their routes are in `test_data_audit_triggers.py`; this file runs every route
from there that has a Postgres form, and adds the Postgres-only ones (ON CONFLICT DO NOTHING, MERGE,
TRUNCATE). Unless a test says otherwise, it runs as the application role (`pg_roles.py`), which
does not own the table and holds UPDATE, DELETE and TRUNCATE on it, so the triggers are what refuse.
The application guard's listeners are switched off where the refusal must come from the database.
"""

from datetime import UTC, datetime

import pg_roles
import pytest
from backend_support import ANALYST_ID, ANALYST_PASSWORD, ANALYST_USERNAME, LEAD_ID, LEAD_TOKEN
from fastapi.testclient import TestClient
from sqlalchemy import delete, insert, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_data_audit_triggers import CHANGED, OVERWRITTEN, REMOVED, ROUTES, attempt, guards_disabled
from test_queue_api import add_case

from socboard import database, migrate
from socboard.audit import append_audit
from socboard.audit_guard import AuditImmutableError
from socboard.models import AuditEntry, User
from socboard.passwords import hash_password

pytestmark = pytest.mark.postgres_only("Postgres triggers, roles and catalogue (migration 0002)")

AUDIT = AuditEntry.__table__
TRUNCATED = "Audit entries cannot be truncated."
TRIGGERS = ("audit_entries_no_delete", "audit_entries_no_reuse_id", "audit_entries_no_truncate", "audit_entries_no_update")
FUNCTIONS = (
    "audit_entries_refuse_delete",
    "audit_entries_refuse_reused_id",
    "audit_entries_refuse_truncate",
    "audit_entries_refuse_update",
)
AT = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def sqlite_only_route(name: str) -> bool:
    """Routes of `test_data_audit_triggers.ROUTES` with no Postgres form.

    INSERT OR REPLACE and REPLACE, SQLite's upsert construct, a stdlib sqlite3 cursor, and the
    case-folded spellings `AUDIT_ENTRIES` and `Audit_Entries` of a lightweight table (SQLite folds an
    unquoted name's case, Postgres keeps a quoted one, so on Postgres those name no table).
    """
    return (
        any(word in name for word in ("replace", "on_conflict", "upsert"))
        or name.startswith("dbapi_cursor_")
        or "light_AUDIT_ENTRIES" in name
        or "light_Audit_Entries" in name
    )


POSTGRES_ROUTES = sorted(name for name in ROUTES if not sqlite_only_route(name))


# --- fixtures -------------------------------------------------------------------------------------


@pytest.fixture
def roles():
    made = pg_roles.create()
    try:
        yield made
    finally:
        pg_roles.drop(made)


def _engine(url):
    return database.create_db_engine(url)


@pytest.fixture
def owner_engine(roles):
    engine = _engine(roles.owner_url)
    yield engine
    engine.dispose()


@pytest.fixture
def app_engine(roles):
    engine = _engine(roles.app_url)
    yield engine
    engine.dispose()


@pytest.fixture
def seeded(roles, owner_engine, app_engine):
    """As the owner: the two test users, a case and one committed audit entry. Tests use the app role."""
    with owner_engine.begin() as conn:
        conn.execute(
            insert(User.__table__),
            [
                dict(id=ANALYST_ID, username=ANALYST_USERNAME, display_name="A", role="analyst",
                     password_hash=hash_password(ANALYST_PASSWORD)),
                dict(id=LEAD_ID, username="test-lead", display_name="L", role="lead", password_hash="x"),
            ],
        )  # fmt: skip
    add_case(roles.owner_url, 1, status="Investigating")
    with owner_engine.begin() as conn:
        entry_id = conn.execute(
            insert(AUDIT).values(case_id=1, actor_id=ANALYST_ID, action="claimed", detail="original", at=AT)
        ).inserted_primary_key[0]
    seeded = {"engine": app_engine, "user_id": ANALYST_ID, "case_id": 1, "entry_id": entry_id, "roles": roles}
    seeded["before"] = rows_now(owner_engine)
    return seeded


def rows_now(engine, table="audit_entries"):
    with engine.connect() as conn:
        return [tuple(row) for row in conn.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()]


def all_rows(engine):
    return {table: rows_now(engine, table) for table in ("users", "alerts", "cases", "notes", "audit_entries")}


def triggers(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tgname, tgenabled FROM pg_trigger "
                "WHERE tgrelid = 'public.audit_entries'::regclass AND NOT tgisinternal ORDER BY tgname"
            )
        ).all()
    return [tuple(row) for row in rows]


def functions(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public' AND p.proname LIKE 'audit_entries_%' ORDER BY 1"
            )
        ).all()
    return [row[0] for row in rows]


def refused(engine, statement, message, parameters=None):
    """Run `statement` as one transaction and return the database's error; nothing is committed."""
    with engine.connect() as conn:
        with pytest.raises(IntegrityError) as info:
            conn.execute(text(statement) if isinstance(statement, str) else statement, parameters or {})
        conn.rollback()
    error = info.value.orig
    assert error.diag.message_primary == message
    assert error.diag.sqlstate == "23000"
    return error


# --- the migration --------------------------------------------------------------------------------


def test_the_migration_creates_four_triggers_all_enabled_always(owner_engine):
    assert triggers(owner_engine) == [(name, "A") for name in TRIGGERS]
    assert functions(owner_engine) == list(FUNCTIONS)


def test_a_second_migrate_changes_nothing_and_a_downgrade_removes_triggers_and_functions(roles, owner_engine):
    migrate.upgrade(roles.owner_url)
    assert triggers(owner_engine) == [(name, "A") for name in TRIGGERS]
    migrate.downgrade(roles.owner_url, "0001")
    assert triggers(owner_engine) == []
    assert functions(owner_engine) == []
    migrate.upgrade(roles.owner_url)
    assert triggers(owner_engine) == [(name, "A") for name in TRIGGERS]


# --- the routes of the SQLite tests that have a Postgres form -------------------------------------


@pytest.mark.parametrize("name", POSTGRES_ROUTES)
def test_the_database_refuses_the_route_with_the_trigger_message(seeded, name):
    kind, message, function = ROUTES[name]
    with guards_disabled():
        error = attempt(seeded, kind, function)
    assert not isinstance(error, AuditImmutableError)
    assert error.diag.message_primary == message  # the fixed text: nothing else, no id, no detail
    assert error.diag.sqlstate == "23000"
    assert "original" not in str(error) and str(seeded["entry_id"]) not in error.diag.message_primary
    assert rows_now(seeded["engine"]) == seeded["before"]


def test_every_sqlite_route_is_either_run_here_or_has_no_postgres_form():
    assert POSTGRES_ROUTES
    assert set(POSTGRES_ROUTES) | {name for name in ROUTES if sqlite_only_route(name)} == set(ROUTES)


# --- Postgres-only routes -------------------------------------------------------------------------


def _row(seeded, detail, with_id=True):
    row = dict(case_id=seeded["case_id"], actor_id=seeded["user_id"], action="claimed", detail=detail, at=AT)
    if with_id:
        row["id"] = seeded["entry_id"]
    return row


def test_a_plain_insert_with_an_existing_id_gets_the_overwrite_text_not_a_key_error(seeded):
    with guards_disabled():
        refused(seeded["engine"], insert(AUDIT).values(**_row(seeded, "again")), OVERWRITTEN)
    assert rows_now(seeded["engine"]) == seeded["before"]


def test_on_conflict_do_update_is_refused(seeded):
    statement = pg_insert(AUDIT).values(**_row(seeded, "upserted"))
    statement = statement.on_conflict_do_update(index_elements=["id"], set_={"detail": "upserted"})
    with guards_disabled():
        refused(seeded["engine"], statement, OVERWRITTEN)
    assert rows_now(seeded["engine"]) == seeded["before"]


def test_on_conflict_do_nothing_with_an_existing_id_is_refused_before_the_row_is_skipped(seeded):
    # VERIFY of the issue: the BEFORE INSERT trigger fires before Postgres decides to skip the row.
    statement = pg_insert(AUDIT).values(**_row(seeded, "ignored")).on_conflict_do_nothing()
    with guards_disabled():
        refused(seeded["engine"], statement, OVERWRITTEN)
    assert rows_now(seeded["engine"]) == seeded["before"]


@pytest.mark.parametrize(
    ("action", "message"),
    [("UPDATE SET detail = 'merged'", CHANGED), ("DELETE", REMOVED)],
    ids=["merge_matched_update", "merge_matched_delete"],
)
def test_merge_when_matched_is_refused(seeded, action, message):
    statement = (
        "MERGE INTO audit_entries AS a USING (SELECT :id AS id) AS s ON a.id = s.id "
        f"WHEN MATCHED THEN {action}"
    )
    refused(seeded["engine"], statement, message, {"id": seeded["entry_id"]})
    assert rows_now(seeded["engine"]) == seeded["before"]


def test_raw_sql_update_and_delete_are_refused(seeded):
    refused(seeded["engine"], "UPDATE audit_entries SET detail = 'changed'", CHANGED)
    refused(seeded["engine"], "DELETE FROM audit_entries", REMOVED)
    assert rows_now(seeded["engine"]) == seeded["before"]


def test_an_orm_change_and_delete_are_refused_by_the_database(seeded):
    with guards_disabled():
        for change, message in ((lambda e: setattr(e, "detail", "changed"), CHANGED), (None, REMOVED)):
            with Session(seeded["engine"]) as session:
                entry = session.get(AuditEntry, seeded["entry_id"])
                if change is None:
                    session.delete(entry)
                else:
                    change(entry)
                with pytest.raises(IntegrityError) as info:
                    session.flush()
                session.rollback()
            assert info.value.orig.diag.message_primary == message
    assert rows_now(seeded["engine"]) == seeded["before"]


# `RESTART IDENTITY` also resets the id sequence, which only the sequence's owner may do, so the
# application role gets a privilege error before any trigger fires. That case runs as the owner: the
# owner passes the privilege check, so the trigger is what answers.
@pytest.mark.parametrize(
    ("statement", "as_owner"),
    [
        ("TRUNCATE audit_entries", False),
        ("TRUNCATE notes, audit_entries", False),
        ("TRUNCATE audit_entries RESTART IDENTITY", True),
        ("TRUNCATE cases CASCADE", False),
        ("TRUNCATE users CASCADE", False),
    ],
)
def test_truncate_is_refused_and_every_table_keeps_its_rows(seeded, owner_engine, statement, as_owner):
    before = all_rows(owner_engine)
    with guards_disabled():
        refused(owner_engine if as_owner else seeded["engine"], statement, TRUNCATED)
    assert all_rows(owner_engine) == before


def test_the_application_role_cannot_restart_the_id_sequence(seeded, app_engine):
    with app_engine.connect() as conn:
        with guards_disabled():
            with pytest.raises(DBAPIError):  # must be owner of the sequence
                conn.exec_driver_sql("TRUNCATE audit_entries RESTART IDENTITY")
        conn.rollback()
    assert rows_now(app_engine) == seeded["before"]


def test_after_a_refused_restart_identity_the_next_id_continues(seeded, app_engine, owner_engine):
    with guards_disabled():
        refused(owner_engine, "TRUNCATE audit_entries RESTART IDENTITY", TRUNCATED)
    with app_engine.begin() as conn:
        new_id = conn.execute(insert(AUDIT).values(**_row(seeded, "next", with_id=False))).inserted_primary_key[0]
    assert new_id > seeded["entry_id"]


# --- append_audit and the guard, as the application role ------------------------------------------


def test_append_audit_appends_with_ascending_ids_after_refused_attempts(seeded, app_engine):
    with guards_disabled():
        refused(app_engine, insert(AUDIT).values(**_row(seeded, "again")), OVERWRITTEN)
        refused(app_engine, "UPDATE audit_entries SET detail = 'x'", CHANGED)
    ids = []
    for detail in ("first", "second"):
        with Session(app_engine) as session:  # the guard is on here
            entry = append_audit(session, case_id=1, actor_id=ANALYST_ID, action="note_added", detail=detail)
            session.commit()
            ids.append(entry.id)
    assert seeded["entry_id"] < ids[0] < ids[1]  # a refused insert may use up a sequence value: gaps are allowed
    assert [row[4] for row in rows_now(app_engine)] == ["original", "first", "second"]


def test_the_guard_lets_a_normal_insert_through_and_still_refuses_writes_on_postgres(seeded, app_engine):
    with Session(app_engine) as session:
        append_audit(session, case_id=1, actor_id=ANALYST_ID, action="note_added", detail="guarded")
        session.commit()
    for statement in (
        update(AUDIT).values(detail="changed"),
        delete(AUDIT),
        pg_insert(AUDIT).values(**_row(seeded, "u")).on_conflict_do_update(index_elements=["id"], set_={"detail": "u"}),
    ):
        with Session(app_engine) as session:
            with pytest.raises(AuditImmutableError):
                session.execute(statement)
    assert [row[4] for row in rows_now(app_engine)] == ["original", "guarded"]


# --- the application role cannot switch the rule off ----------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE audit_entries DISABLE TRIGGER ALL",
        "DROP TRIGGER audit_entries_no_update ON audit_entries",
        "CREATE TABLE public.not_allowed (id integer)",
    ],
)
def test_the_application_role_cannot_disable_or_drop_the_triggers_or_run_ddl(seeded, app_engine, owner_engine, statement):
    with app_engine.connect() as conn:
        with guards_disabled():
            with pytest.raises(DBAPIError):  # the message text is not pinned (VERIFY in the issue)
                conn.exec_driver_sql(statement)
        conn.rollback()
    assert triggers(owner_engine) == [(name, "A") for name in TRIGGERS]


def test_the_application_role_cannot_set_replica_mode_and_the_trigger_still_refuses(seeded, app_engine):
    with app_engine.connect() as conn:
        with pytest.raises(DBAPIError):
            conn.exec_driver_sql("SET session_replication_role = replica")
        conn.rollback()
    refused(app_engine, "UPDATE audit_entries SET detail = 'changed'", CHANGED)


def test_a_superuser_in_replica_mode_is_still_refused_because_the_triggers_are_enabled_always(seeded, roles):
    import db_target

    superuser = database.create_db_engine(db_target.server_url().set(database=roles.database))
    try:
        with superuser.connect() as conn:
            conn.exec_driver_sql("SET session_replication_role = replica")
            with pytest.raises(IntegrityError) as info:
                conn.exec_driver_sql("UPDATE audit_entries SET detail = 'changed'")
            conn.rollback()
        assert info.value.orig.diag.message_primary == CHANGED
    finally:
        superuser.dispose()
    assert rows_now(seeded["engine"]) == seeded["before"]


def test_the_app_works_as_the_application_role(seeded, monkeypatch):
    from socboard.app import create_app

    monkeypatch.setenv("SOCBOARD_DATABASE_URL", seeded["roles"].app_url)
    client = TestClient(create_app())
    login = client.post("/api/login", json={"username": ANALYST_USERNAME, "password": ANALYST_PASSWORD})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    assert client.post("/api/cases/1/claim", headers=headers).status_code == 200
    lead = {"Authorization": f"Bearer {LEAD_TOKEN}"}  # the audit read is Lead-only
    audit = client.get("/api/cases/1/audit", headers=lead)
    assert audit.status_code == 200
    assert [entry["action"] for entry in audit.json()] == ["claimed", "claimed"]


def test_no_role_password_is_sent_to_the_server(monkeypatch):
    """QA finding on #193: a failed CREATE ROLE would echo its statement, so the statement holds the
    SCRAM verifier, never the password; the roles still log in with the password (the next tests)."""
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    passwords, statements = [], []
    real = pg_roles.secrets.token_hex

    def remember(n):
        value = real(n)
        if n == 16:
            passwords.append(value)
        return value

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    monkeypatch.setattr(pg_roles.secrets, "token_hex", remember)
    event.listen(Engine, "before_cursor_execute", record)
    try:
        made = pg_roles.create()
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    try:
        assert len(passwords) == 2 and any("CREATE ROLE" in s for s in statements)
        for password in passwords:
            assert not any(password in statement for statement in statements)
        for url in (made.owner_url, made.app_url):  # the password still works for a login
            engine = _engine(url)
            try:
                with engine.connect() as conn:
                    assert conn.exec_driver_sql("SELECT 1").scalar() == 1
            finally:
                engine.dispose()
    finally:
        pg_roles.drop(made)


def test_the_scram_verifier_holds_no_part_of_the_password():
    verifier = pg_roles.scram_verifier("0123456789abcdef0123456789abcdef", salt=b"0" * 16)
    assert verifier.startswith("SCRAM-SHA-256$4096:") and "0123456789abcdef" not in verifier


def test_no_trigger_text_holds_a_row_value(seeded):
    with guards_disabled():
        error = refused(seeded["engine"], "UPDATE audit_entries SET detail = 'zz-secret-77'", CHANGED)
    assert "zz-secret-77" not in str(error) and "original" not in str(error)
