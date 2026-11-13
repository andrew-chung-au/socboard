"""The audit guard's remaining DDL and id gaps (#99): L1 to L5 from #11's QA findings.

Every refusal runs with the guard on, on a migrated temporary database, and each one checks that
nothing changed (rows, `sqlite_sequence`, `sqlite_master`) and that `append_audit` still works
afterwards. Raw SQL is not refused (a recorded limit); where a test needs a route past the guard it
uses `exec_driver_sql` on purpose.
"""

import sqlite3
from contextlib import nullcontext

import pytest
from data_support import (
    NOW,
    insert_audit_row,
    insert_case,
    insert_user,
    isolated_database_env,  # noqa: F401
    migrated_engine,  # noqa: F401
    no_default_database_file,  # noqa: F401
    sqlite_url,
)
from sqlalchemy import (
    DDL,
    Index,
    bindparam,
    column,
    delete,
    func,
    insert,
    literal,
    literal_column,
    select,
    table,
    text,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import DropIndex
from test_data_audit_triggers import guards_disabled

from socboard import audit_guard, migrate
from socboard.audit import append_audit
from socboard.audit_guard import (
    IMMUTABLE_MESSAGE,
    AuditImmutableError,
    allow_schema_changes,
    refused_statement,
)
from socboard.models import AuditEntry

AUDIT = AuditEntry.__table__
BIG = 2**63 - 1
ID_CHECK = "ck_audit_entries_id_positive"
OVERWRITTEN = "Audit entries cannot be overwritten."
INDEX = "ix_audit_entries_case_id"
ALL_COLUMNS = ["id", "case_id", "actor_id", "action", "detail", "at"]


@pytest.fixture
def seeded(migrated_engine):  # noqa: F811
    """A user, a case and one audit entry (id 1), committed."""
    with migrated_engine.begin() as conn:
        user_id = insert_user(conn)
        case_id = insert_case(conn)
        entry_id = insert_audit_row(conn, case_id=case_id, actor_id=user_id, detail="original")
    return {"engine": migrated_engine, "user_id": user_id, "case_id": case_id, "entry_id": entry_id}


def _row(seeded, **extra):
    return dict(
        case_id=seeded["case_id"], actor_id=seeded["user_id"], action="claimed", detail="new", at=NOW, **extra
    )


def snapshot(engine):
    """Rows, the audit sequence value and the whole schema, read with raw SQL (never refused)."""
    with engine.connect() as conn:
        rows = conn.exec_driver_sql("SELECT id, detail FROM audit_entries ORDER BY id").all()
        seq = conn.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name = 'audit_entries'").scalar()
        schema = conn.exec_driver_sql("SELECT type, name FROM sqlite_master ORDER BY type, name").all()
    return [tuple(r) for r in rows], seq, [tuple(r) for r in schema]


def assert_append_audit_works(seeded):
    engine = seeded["engine"]
    rows_before, seq_before, _ = snapshot(engine)
    with Session(engine) as session:
        entry = append_audit(
            session, case_id=seeded["case_id"], actor_id=seeded["user_id"], action="note_added", detail="after"
        )
        entry_id = entry.id
        session.commit()
    rows, seq, _ = snapshot(engine)
    assert rows == [*rows_before, (entry_id, "after")]
    assert entry_id == seq_before + 1 == seq


def _conn(engine, statement, parameters=None):
    with engine.connect() as conn:
        conn.execute(statement, parameters)


def _session(engine, statement, parameters=None):
    with Session(engine) as session:
        session.execute(statement, parameters)


def _light(name):
    return table(name, column("id"), column("detail"))


# --- L1: an explicit id ---------------------------------------------------------------------------


def _orm_flush(engine, seeded, id_):
    with Session(engine) as session:
        session.add(AuditEntry(id=id_, **_row(seeded)))
        session.flush()


def _orm_bulk_insert_mappings(engine, seeded, id_):
    with Session(engine) as session:
        session.bulk_insert_mappings(AuditEntry, [_row(seeded, id=id_)])


def _from_select(seeded, id_):
    source = select(
        literal(id_), literal(seeded["case_id"]), literal(seeded["user_id"]), literal("claimed"), literal("n"), literal(NOW)
    )
    return insert(AUDIT).from_select(ALL_COLUMNS, source)


ID_ROUTES = {
    "values_kwargs": lambda e, s, i: _conn(e, insert(AUDIT).values(**_row(s, id=i))),
    "values_multi_row": lambda e, s, i: _conn(e, insert(AUDIT).values([_row(s), _row(s, id=i)])),
    "parameters_dict_connection": lambda e, s, i: _conn(e, insert(AUDIT), _row(s, id=i)),
    "parameters_list_connection": lambda e, s, i: _conn(e, insert(AUDIT), [_row(s), _row(s, id=i)]),
    "parameters_dict_session": lambda e, s, i: _session(e, insert(AUDIT), _row(s, id=i)),
    "parameters_list_session": lambda e, s, i: _session(e, insert(AUDIT), [_row(s), _row(s, id=i)]),
    "orm_insert_parameters_session": lambda e, s, i: _session(e, insert(AuditEntry), [_row(s, id=i)]),
    "from_select": lambda e, s, i: _conn(e, _from_select(s, i)),
    "orm_entry_flush": _orm_flush,
    "bulk_insert_mappings": _orm_bulk_insert_mappings,
    "lightweight_lower": lambda e, s, i: _conn(e, insert(_light("audit_entries")).values(id=i, detail="x")),
    "lightweight_upper": lambda e, s, i: _conn(e, insert(_light("AUDIT_ENTRIES")).values(id=i, detail="x")),
    "lightweight_mixed": lambda e, s, i: _conn(e, insert(_light("Audit_Entries")).values(id=i, detail="x")),
}


@pytest.mark.parametrize("route", ID_ROUTES)
def test_an_explicit_id_is_refused_whichever_way_it_arrives(seeded, route):
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError) as info:
        ID_ROUTES[route](seeded["engine"], seeded, 7)
    assert str(info.value) == IMMUTABLE_MESSAGE
    assert snapshot(seeded["engine"]) == before
    assert_append_audit_works(seeded)


@pytest.mark.parametrize("route", ["values_kwargs", "orm_entry_flush", "parameters_dict_connection"])
def test_an_explicit_id_of_two_to_the_63_minus_1_is_refused_and_the_sequence_does_not_move(seeded, route):
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError):
        ID_ROUTES[route](seeded["engine"], seeded, BIG)
    assert snapshot(seeded["engine"]) == before  # no row, same seq
    assert_append_audit_works(seeded)


def test_an_explicit_id_is_still_refused_inside_allow_schema_changes(seeded):
    before = snapshot(seeded["engine"])
    with allow_schema_changes():
        with pytest.raises(AuditImmutableError):
            ID_ROUTES["values_kwargs"](seeded["engine"], seeded, 7)
        with pytest.raises(AuditImmutableError):
            ID_ROUTES["orm_entry_flush"](seeded["engine"], seeded, 7)
    assert snapshot(seeded["engine"]) == before


def test_the_guard_function_refuses_a_positional_row_with_an_id_and_a_none_id_passes():
    row = (7, 1, 1, "claimed", "d", NOW)
    assert refused_statement(insert(AUDIT).values([row, row]))
    assert refused_statement(insert(AUDIT).values(row))
    assert refused_statement(insert(AUDIT), {"ID": 7})
    assert refused_statement(insert(AUDIT), ([{"id": None}, {"id": 7}], {}))
    assert not refused_statement(insert(AUDIT).values([(None, *row[1:])]))
    assert not refused_statement(insert(AUDIT), {"id": None})
    assert not refused_statement(insert(AUDIT).from_select(ALL_COLUMNS[1:], select(literal(1), literal(1), literal("a"), literal("b"), literal(NOW))))


def test_an_insert_with_no_id_or_a_none_id_is_not_refused(seeded):
    engine = seeded["engine"]
    plain = _row(seeded)
    none_id = _row(seeded, id=None)

    def orm(with_id_none):
        def run():
            with Session(engine) as session:
                entry = AuditEntry(id=None, **plain) if with_id_none else AuditEntry(**plain)
                session.add(entry)
                session.commit()

        return run

    runs = [  # (what to run, rows it adds)
        (lambda: _conn_commit(engine, insert(AUDIT).values(**plain)), 1),
        (lambda: _conn_commit(engine, insert(AUDIT).values(**none_id)), 1),
        (lambda: _conn_commit(engine, insert(AUDIT), plain), 1),
        (lambda: _conn_commit(engine, insert(AUDIT), [none_id, none_id]), 2),  # executemany
        (orm(False), 1),
        (orm(True), 1),
    ]
    expected = 1
    for run, added in runs:
        run()
        expected += added
        assert len(snapshot(engine)[0]) == expected
    assert_append_audit_works(seeded)


def _conn_commit(engine, statement, parameters=None):
    with engine.begin() as conn:
        conn.execute(statement, parameters)


# --- L2: the rendered text of a DDL(...) ----------------------------------------------------------

RENDER_REFUSALS = {
    "context_value": lambda: DDL("DROP TRIGGER %(n)s", context={"n": "audit_entries_no_update"}),
    "context_split_over_two_values": lambda: DDL(
        "DROP TRIGGER %(a)s%(b)s", context={"a": "audit_entries_no_", "b": "update"}
    ),
    "context_table_name": lambda: DDL("DROP TABLE %(n)s", context={"n": "audit_entries"}),
    "fallback_missing_key_names_a_context_value": lambda: DDL(
        "DROP TRIGGER %(missing)s", context={"n": "audit_entries_no_update"}
    ),
    "fallback_bad_format_names_a_context_value": lambda: DDL(
        "DROP TRIGGER %z", context={"n": "audit_entries_no_delete"}
    ),
    "fallback_missing_key_names_in_the_template": lambda: DDL("DROP TRIGGER audit_entries_no_reuse_id -- %(missing)s"),
    "double_percent_in_the_template": lambda: DDL("DROP TRIGGER audit_entries_no_update -- 100%%"),
}


@pytest.mark.parametrize("name", RENDER_REFUSALS)
def test_a_ddl_whose_rendered_text_names_the_table_or_a_trigger_is_refused(seeded, name):
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError):
        _conn(seeded["engine"], RENDER_REFUSALS[name]())
    after = snapshot(seeded["engine"])
    assert after == before
    assert {"audit_entries_no_update", "audit_entries_no_delete", "audit_entries_no_reuse_id"} <= {
        n for kind, n in after[2] if kind == "trigger"
    }
    assert_append_audit_works(seeded)


def test_a_ddl_whose_rendered_text_names_nothing_is_not_refused():
    assert not refused_statement(DDL("SELECT '%%' /* %(n)s */", context={"n": "audit_entries_archive"}))
    assert not refused_statement(DDL("SELECT %(missing)s", context={"n": "audit_entries_archive"}))
    assert not refused_statement(DDL("DROP TRIGGER %(a)s%(b)s", context={"a": "audit_entries_no_", "b": "update_log"}))


# --- L3: DropIndex --------------------------------------------------------------------------------


def _real_index():
    return next(index for index in AUDIT.indexes if index.name == INDEX)


def _lightweight_index():
    lightweight = table("audit_entries", column("case_id"))
    return Index(INDEX, lightweight.c.case_id)


INDEX_REFUSALS = {
    "bound_to_the_real_table": lambda: DropIndex(_real_index()),
    "built_on_a_lightweight_column": lambda: DropIndex(_lightweight_index()),
    "given_by_name_only": lambda: DropIndex(Index(INDEX)),
    "name_only_upper_case": lambda: DropIndex(Index(INDEX.upper())),
    "if_exists": lambda: DropIndex(Index(INDEX), if_exists=True),
}


@pytest.mark.parametrize("name", INDEX_REFUSALS)
def test_drop_index_of_an_audit_index_is_refused_and_the_index_remains(seeded, name):
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError):
        _conn(seeded["engine"], INDEX_REFUSALS[name]())
    after = snapshot(seeded["engine"])
    assert ("index", INDEX) in after[2]
    assert after == before
    assert_append_audit_works(seeded)


def test_every_index_of_audit_entries_is_in_the_index_names_constant(seeded):
    with seeded["engine"].connect() as conn:
        names = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'audit_entries' "
            "AND name NOT LIKE 'sqlite_autoindex_%'"
        ).scalars().all()
    assert INDEX in names
    missing = set(names) - set(audit_guard.INDEX_NAMES)
    assert not missing, f"add {sorted(missing)} to INDEX_NAMES in socboard/audit_guard.py"


# --- L4: pragmas and sqlite_sequence --------------------------------------------------------------


def _pragma_texts():
    texts = []
    for pragma in ("ignore_check_constraints", "writable_schema"):
        for keyword, spelled in (
            ("pragma", pragma.lower()),
            ("PRAGMA", pragma.upper()),
            ("Pragma", pragma.title()),
        ):
            for form in ("= 1", "=ON", "= ON", "(1)"):
                texts.append(f"{keyword} {spelled} {form}")
        texts.append(f"PRAGMA main.{pragma} = ON")
        texts.append(f"PRAGMA MAIN.{pragma.upper()}=1")
    return texts


RAW_NEGATIVE_ID = (
    "INSERT INTO audit_entries (id, case_id, actor_id, action, detail, at) "
    "VALUES (-1, ?, ?, 'claimed', 'bad', '2026-11-12 09:00:00')"
)


@pytest.mark.parametrize("sql", _pragma_texts())
def test_a_dangerous_pragma_is_refused_and_id_minus_1_is_still_refused_by_the_check(seeded, sql):
    before = snapshot(seeded["engine"])
    with seeded["engine"].connect() as conn:
        with pytest.raises(AuditImmutableError):
            conn.execute(DDL(sql))
        with pytest.raises(IntegrityError) as info:  # raw SQL passes the guard; the CHECK refuses
            conn.exec_driver_sql(RAW_NEGATIVE_ID, (seeded["case_id"], seeded["user_id"]))
        conn.rollback()
    assert ID_CHECK in str(info.value.orig)
    assert snapshot(seeded["engine"]) == before
    assert_append_audit_works(seeded)


def test_a_dangerous_pragma_is_not_lifted_by_allow_schema_changes(seeded):
    with allow_schema_changes():
        for sql in ("PRAGMA ignore_check_constraints = ON", "PRAGMA main.writable_schema = 1"):
            with pytest.raises(AuditImmutableError):
                _conn(seeded["engine"], DDL(sql))


def test_the_migration_runner_still_works_on_a_new_database(tmp_path):
    path = tmp_path / "fresh.db"
    migrate.upgrade(sqlite_url(path))
    raw = sqlite3.connect(path)
    try:
        names = {row[0] for row in raw.execute("SELECT name FROM sqlite_master")}
    finally:
        raw.close()
    assert {"audit_entries", "audit_entries_no_update", INDEX} <= names


SEQUENCE = ("sqlite_sequence", "SQLITE_SEQUENCE", "Sqlite_Sequence")


def _sequence_table(name):
    return table(name, column("name"), column("seq"))


SEQUENCE_REFUSALS = {}
for _spelling in SEQUENCE:
    SEQUENCE_REFUSALS[f"update_{_spelling}"] = lambda s=_spelling: update(_sequence_table(s)).values(seq=0)
    SEQUENCE_REFUSALS[f"update_where_{_spelling}"] = lambda s=_spelling: (
        update(_sequence_table(s)).where(_sequence_table(s).c.name == "audit_entries").values(seq=BIG)
    )
    SEQUENCE_REFUSALS[f"insert_{_spelling}"] = lambda s=_spelling: insert(_sequence_table(s)).values(
        name="audit_entries", seq=BIG
    )
    SEQUENCE_REFUSALS[f"delete_{_spelling}"] = lambda s=_spelling: delete(_sequence_table(s))
SEQUENCE_REFUSALS["ddl_update"] = lambda: DDL("UPDATE sqlite_sequence SET seq = %(n)s", context={"n": BIG})
SEQUENCE_REFUSALS["ddl_delete_upper"] = lambda: DDL("DELETE FROM SQLITE_SEQUENCE")


@pytest.mark.parametrize("name", SEQUENCE_REFUSALS)
def test_a_write_to_sqlite_sequence_is_refused_and_the_value_is_unchanged(seeded, name):
    before = snapshot(seeded["engine"])
    assert before[1] == 1
    with pytest.raises(AuditImmutableError):
        _conn(seeded["engine"], SEQUENCE_REFUSALS[name]())
    assert snapshot(seeded["engine"]) == before
    assert_append_audit_works(seeded)


def test_a_write_to_sqlite_sequence_is_not_lifted_by_allow_schema_changes(seeded):
    with allow_schema_changes():
        for name in ("update_sqlite_sequence", "ddl_update"):
            with pytest.raises(AuditImmutableError):
                _conn(seeded["engine"], SEQUENCE_REFUSALS[name]())


def test_a_read_of_sqlite_sequence_is_not_refused(seeded):
    sequence = _sequence_table("sqlite_sequence")
    with seeded["engine"].connect() as conn:
        value = conn.execute(select(sequence.c.seq).where(sequence.c.name == "audit_entries")).scalar_one()
    assert value == snapshot(seeded["engine"])[1]


# --- near misses and what must not change ---------------------------------------------------------


@pytest.mark.parametrize("name", ["audit_entries_archive", "xaudit_entries", "audit_entries2"])
def test_ddl_and_dml_on_a_near_miss_table_name_are_allowed(seeded, name):
    near = _light(name)
    with seeded["engine"].begin() as conn:
        conn.execute(DDL(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, detail TEXT)"))
        conn.execute(insert(near).values(id=5, detail="a"))  # an explicit id is only refused on audit_entries
        conn.execute(insert(near), [{"id": 6, "detail": "b"}])
        conn.execute(update(near).where(near.c.id == 5).values(detail="c"))
        conn.execute(delete(near).where(near.c.id == 6))
        assert conn.execute(select(near.c.detail)).scalars().all() == ["c"]
        conn.execute(DDL(f"DROP TABLE {name}"))
    assert_append_audit_works(seeded)


def test_a_trigger_named_audit_entries_no_update_log_is_allowed(seeded):
    with seeded["engine"].begin() as conn:
        conn.execute(DDL("CREATE TABLE audit_entries_archive (id INTEGER PRIMARY KEY, detail TEXT)"))
        conn.execute(
            DDL("CREATE TRIGGER audit_entries_no_update_log AFTER INSERT ON audit_entries_archive BEGIN SELECT 1; END")
        )
        conn.execute(DDL("DROP TRIGGER audit_entries_no_update_log"))
        conn.execute(DDL("DROP TABLE audit_entries_archive"))
    assert_append_audit_works(seeded)


def test_an_index_named_ix_audit_entries_archive_case_id_is_allowed(seeded):
    name = "ix_audit_entries_archive_case_id"
    with seeded["engine"].begin() as conn:
        conn.execute(DDL("CREATE TABLE audit_entries_archive (id INTEGER PRIMARY KEY, case_id INTEGER)"))
        conn.execute(DDL(f"CREATE INDEX {name} ON audit_entries_archive (case_id)"))
        conn.execute(DropIndex(Index(name)))
        conn.execute(DDL("DROP TABLE audit_entries_archive"))
    assert not refused_statement(DropIndex(Index(name)))
    assert_append_audit_works(seeded)


def test_a_table_named_sqlite_sequence_archive_is_allowed():
    # SQLite does not let a user create a `sqlite_`-prefixed table, so the guard function is
    # checked without a database.
    archive = _sequence_table("sqlite_sequence_archive")
    assert not refused_statement(update(archive).values(seq=0))
    assert not refused_statement(insert(archive).values(name="a", seq=1))
    assert not refused_statement(delete(archive))
    assert not refused_statement(DDL("DELETE FROM sqlite_sequence_archive"))
    assert not refused_statement(DDL("DROP TABLE sqlite_sequence_archive"))


@pytest.mark.parametrize("sql", ["PRAGMA foreign_keys", "PRAGMA foreign_keys = ON", "PRAGMA user_version"])
def test_other_pragmas_as_ddl_are_not_refused(sql):
    assert not refused_statement(DDL(sql))


def test_one_database_survives_every_refusal_and_append_audit_still_works(seeded):
    engine = seeded["engine"]
    before = snapshot(engine)
    refusals = [lambda r=r: r(engine, seeded, 7) for r in ID_ROUTES.values()]
    for group in (RENDER_REFUSALS, INDEX_REFUSALS, SEQUENCE_REFUSALS):
        refusals += [lambda build=build: _conn(engine, build()) for build in group.values()]
    refusals += [lambda sql=sql: _conn(engine, DDL(sql)) for sql in _pragma_texts()]
    for refusal in refusals:
        with pytest.raises(AuditImmutableError):
            refusal()
    assert snapshot(engine) == before
    assert_append_audit_works(seeded)


# --- L5: INSERT OR IGNORE and ON CONFLICT DO NOTHING raise instead of skipping --------------------


def _raw_duplicate(sql_start, sql_end=""):
    return (
        f"{sql_start} INTO audit_entries (id, case_id, actor_id, action, detail, at) "
        f"VALUES (?, ?, ?, 'claimed', 'dup', '2026-11-12 09:00:00') {sql_end}"
    )


RAW_DUPLICATES = {
    "insert_or_ignore": _raw_duplicate("INSERT OR IGNORE"),
    "on_conflict_do_nothing": _raw_duplicate("INSERT", "ON CONFLICT DO NOTHING"),
}

CORE_DUPLICATES = {
    "or_ignore_prefix": lambda s: insert(AUDIT).prefix_with("OR IGNORE").values(**_row(s, id=s["entry_id"])),
    "on_conflict_do_nothing": lambda s: sqlite_insert(AUDIT).values(**_row(s, id=s["entry_id"])).on_conflict_do_nothing(),
}


@pytest.mark.parametrize("guard", ["on", "off"])
@pytest.mark.parametrize("name", RAW_DUPLICATES)
def test_raw_or_ignore_and_on_conflict_do_nothing_on_an_existing_id_raise_the_trigger_message(seeded, name, guard):
    before = snapshot(seeded["engine"])
    args = (seeded["entry_id"], seeded["case_id"], seeded["user_id"])
    with _guard(guard):
        with seeded["engine"].connect() as conn:
            with pytest.raises(IntegrityError) as info:
                conn.exec_driver_sql(RAW_DUPLICATES[name], args)
            conn.rollback()
    assert OVERWRITTEN in str(info.value.orig)
    assert snapshot(seeded["engine"]) == before


@pytest.mark.parametrize("name", CORE_DUPLICATES)
def test_core_or_ignore_and_on_conflict_do_nothing_on_an_existing_id_are_refused_by_the_guard(seeded, name):
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError):
        _conn(seeded["engine"], CORE_DUPLICATES[name](seeded))
    assert snapshot(seeded["engine"]) == before


@pytest.mark.parametrize("name", CORE_DUPLICATES)
def test_core_or_ignore_and_on_conflict_do_nothing_on_an_existing_id_raise_the_trigger_message_without_the_guard(
    seeded, name
):
    before = snapshot(seeded["engine"])
    with guards_disabled():
        with seeded["engine"].connect() as conn:
            with pytest.raises(IntegrityError) as info:
                conn.execute(CORE_DUPLICATES[name](seeded))
            conn.rollback()
    assert OVERWRITTEN in str(info.value.orig)
    assert snapshot(seeded["engine"]) == before


def _guard(state):
    return nullcontext() if state == "on" else guards_disabled()


# --- #99 round 2: an id through a bound parameter of any name -------------------------------------


def _bound(seeded, **extra):
    """An insert whose `id` is `bindparam("x")`; the other columns are plain values."""
    return insert(AUDIT).values(id=bindparam("x"), **_row(seeded), **extra)


BOUND_ROUTES = {
    "connection_dict": lambda e, s, v: _conn(e, _bound(s), {"x": v}),
    "connection_executemany": lambda e, s, v: _conn(e, _bound(s), [{"x": None}, {"x": v}]),
    "session_dict": lambda e, s, v: _session(e, _bound(s), {"x": v}),
    "session_executemany": lambda e, s, v: _session(e, _bound(s), [{"x": v}, {"x": None}]),
    "connection_kwargs_style": lambda e, s, v: _conn(e, _bound(s), {"x": v, "unused": 1}),
    "other_name": lambda e, s, v: _conn(
        e, insert(AUDIT).values(id=bindparam("whatever"), **_row(s)), {"whatever": v}
    ),
}


@pytest.mark.parametrize("route", BOUND_ROUTES)
@pytest.mark.parametrize("value", [BIG, 7])
def test_an_id_through_a_named_bound_parameter_is_refused(seeded, route, value):
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError):
        BOUND_ROUTES[route](seeded["engine"], seeded, value)
    assert snapshot(seeded["engine"]) == before
    assert_append_audit_works(seeded)


def test_a_named_bound_parameter_executed_with_none_is_not_refused(seeded):
    engine = seeded["engine"]
    statement = insert(AUDIT).values(id=bindparam("x"), **_row(seeded))
    _conn_commit(engine, statement, {"x": None})
    assert len(snapshot(engine)[0]) == 2
    _conn_commit(engine, statement, [{"x": None}, {"x": None}])
    assert len(snapshot(engine)[0]) == 4
    assert_append_audit_works(seeded)


def test_the_guard_function_resolves_a_bound_parameter_against_the_execute_parameters():
    row = dict(case_id=1, actor_id=1, action="a", detail="d", at=NOW)
    statement = insert(AUDIT).values(id=bindparam("x"), **row)
    assert not refused_statement(statement, {"x": None})
    assert not refused_statement(statement, ([{"x": None}, {"x": None}], {}))
    assert not refused_statement(statement)  # no parameter and no value of its own
    assert refused_statement(statement, {"x": 1})
    assert refused_statement(statement, [{"x": None}, {"x": 1}])
    assert refused_statement(statement, ([{"x": 1}], {}))
    assert refused_statement(insert(AUDIT).values(id=bindparam("x", value=5), **row))
    assert refused_statement(insert(AUDIT).values(id=bindparam("x", callable_=lambda: 5), **row))
    assert refused_statement(insert(AUDIT).values([{**row, "id": bindparam("x")}]), {"x": 3})
    assert not refused_statement(insert(AUDIT).values([{**row, "id": bindparam("x")}]), {"x": None})


EXPRESSION_IDS = {
    "literal": lambda: literal(5),
    "literal_column": lambda: literal_column("9223372036854775807"),
    "text": lambda: text("5"),
    "function": lambda: func.abs(-5),
    "bound_expression": lambda: bindparam("x", value=None) + 1,
}


@pytest.mark.parametrize("name", EXPRESSION_IDS)
def test_an_expression_in_the_id_position_is_refused(seeded, name):
    expression = EXPRESSION_IDS[name]()
    row = dict(case_id=seeded["case_id"], actor_id=seeded["user_id"], action="a", detail="d", at=NOW)
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError):
        _conn(seeded["engine"], insert(AUDIT).values(id=expression, **row))
    assert snapshot(seeded["engine"]) == before
    assert_append_audit_works(seeded)


# --- #99 round 2: a pragma or name behind a comment ----------------------------------------------

COMMENTED_PRAGMA_FORMS = [
    "PRAGMA /* x */ {p} = ON",
    "PRAGMA/**/{p} = ON",
    "PRAGMA main./**/{p} = ON",
    "PRAGMA -- c\n {p} = ON",
    "PRAGMA/* a */main/* b */./* c */{p}=1",
    "PRAGMA\n-- c\n-- d\n{p}(1)",
    "pragma /* x */ {p} = on",
    "SELECT '--'; PRAGMA {p} = ON",  # the quotes hide no comment: stripping alone would miss this
    "SELECT '/*'; PRAGMA {p} = ON; SELECT '*/'",
]


@pytest.mark.parametrize("pragma", ["ignore_check_constraints", "writable_schema"])
@pytest.mark.parametrize("form", COMMENTED_PRAGMA_FORMS)
def test_a_dangerous_pragma_behind_a_comment_is_refused(seeded, pragma, form):
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError):
        _conn(seeded["engine"], DDL(form.format(p=pragma)))
    assert snapshot(seeded["engine"]) == before
    assert_append_audit_works(seeded)


@pytest.mark.parametrize("pragma", ["ignore_check_constraints", "writable_schema"])
def test_a_commented_dangerous_pragma_is_not_lifted_by_allow_schema_changes(seeded, pragma):
    with allow_schema_changes():
        for form in COMMENTED_PRAGMA_FORMS[:4]:
            with pytest.raises(AuditImmutableError):
                _conn(seeded["engine"], DDL(form.format(p=pragma)))


@pytest.mark.parametrize(
    "sql",
    [
        "DROP /* x */ TRIGGER /* y */ audit_entries_no_update",
        "DROP TRIGGER -- c\n audit_entries_no_delete",
        "DROP TABLE main./**/audit_entries",
        "DROP TABLE/**/audit_entries",
        "UPDATE /* x */ sqlite_sequence SET seq = 0",
        "DELETE FROM -- c\n sqlite_sequence",
        "UPDATE main./**/sqlite_sequence SET seq = 0",
    ],
)
def test_a_name_beside_a_comment_is_refused(seeded, sql):
    before = snapshot(seeded["engine"])
    with pytest.raises(AuditImmutableError):
        _conn(seeded["engine"], DDL(sql))
    assert snapshot(seeded["engine"]) == before
    assert_append_audit_works(seeded)


@pytest.mark.parametrize(
    "sql",
    [
        "PRAGMA /* x */ foreign_keys = ON",
        "PRAGMA/**/user_version",
        "PRAGMA -- c\n user_version",
        "PRAGMA /* x */ ignore_check_constraints_log = ON",
        "PRAGMA /* x */ writable_schema2 = ON",
        "PRAGMA main./**/xwritable_schema = ON",
        "SELECT 1 /* audit_entries_archive */",
        "SELECT 1 /* audit_entries_archive */ -- sqlite_sequence_archive",
        "DROP TABLE /* x */ audit_entries_archive",
        "DROP TRIGGER/**/audit_entries_no_update_log",
        "DELETE FROM /* x */ sqlite_sequence_archive",
        "SELECT 'audit_/**/entries'",  # a comment inside a name makes two words, not the table name
    ],
)
def test_near_misses_behind_comments_are_still_allowed(sql):
    assert not refused_statement(DDL(sql))


@pytest.mark.parametrize(
    "sql",
    [
        # a comment that names the table is refused: the raw text is matched as well as the comment-stripped text
        "SELECT 1 /* audit_entries */ -- audit_entries_archive",
    ],
)
def test_a_comment_that_names_the_table_is_refused(sql):
    assert refused_statement(DDL(sql))
