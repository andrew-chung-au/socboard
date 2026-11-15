"""Refuse any change to `audit_entries` (the audit trail is append-only, spec section 5).

The database is the first layer: migration 0001 creates three SQLite triggers on `audit_entries`
that abort any UPDATE, any DELETE and any INSERT that reuses an id. This module is the second
layer, and on SQLite the only guard against DDL on the table (the triggers cannot stop a DROP)
(decision record `_docs/decisions/2026-11-12T0834Z-data-model.md`; Postgres, below). Three guards act at the application
layer:

1. Mapper events on `AuditEntry` (registered in `socboard/models.py`) raise on an ORM update or
   delete at flush time.
2. A `do_orm_execute` hook on `Session` refuses a statement that `refused_statement` rejects, in
   ORM or Core form.
3. One `before_execute` listener on `Engine` applies the same check to every Core statement on a
   connection, which also covers `Session.bulk_update_mappings`.

`refused_statement` rejects, for the table `audit_entries` only:

- an UPDATE or DELETE;
- an INSERT that can overwrite an existing entry: one with an on-conflict-do-update (or MySQL
  on-duplicate-key-update) clause, or with an `OR REPLACE` or `REPLACE` prefix. A plain INSERT
  (what `append_audit` does) passes. `ON CONFLICT DO NOTHING` and `OR IGNORE` also pass the
  guard, but they do not skip a duplicate: on an existing audit id the database trigger
  `audit_entries_no_reuse_id` raises, so nothing may de-duplicate audit entries that way;
- an INSERT that can supply a non-NULL `id` (#99, #107; see `supplies_audit_id`): the id is
  assigned by the database, and an id near 2^63-1 would make every later `append_audit` fail.
  `allow_schema_changes()` does not lift this;
- a DDL construct aimed at the table (`DropTable`, `CreateTable`, an index or constraint on it,
  an Alembic `ALTER TABLE` construct, or `DDL(...).against(table)`): any object with a string
  `name` after unwrapping aliases counts, so a lightweight `table("audit_entries")` is covered
  too; `DropIndex` for the index of the table, by its name (`INDEX_NAMES`) or by the table it is
  built on; and a `DDL("...")` whose text (`ddl_text`) names `audit_entries` or one of the three
  trigger names as a word;
- a `DDL(...)` whose text contains `ignore_check_constraints` or `writable_schema` as a word
  (which would switch off the CHECK or allow edits of the schema; any spelling, quoting or schema
  prefix), and any UPDATE, INSERT, DELETE or `DDL(...)` naming `sqlite_sequence` (which holds the
  next audit id). None of these is lifted by `allow_schema_changes()`. A SELECT from
  `sqlite_sequence` passes.
  Not covered: a custom `ExecutableDDLElement` with `@compiles` that emits `DROP TRIGGER`, and
  `CreateIndex` of a new index on a lightweight `audit_entries` column.

On Postgres (#195) the rules are the same and behave the same, since nothing is compiled. The
database layer there is migration 0002's four `ENABLE ALWAYS` triggers (update, delete, reused id,
truncate) and the rule that only the table's owner may alter or drop it or its triggers, so the guard
is an extra layer and not the only one against DDL. The SQLite-only rules (`sqlite_sequence`, the
two pragma names, the `OR REPLACE` prefix) never match a Postgres statement and stay. Not refused
on Postgres, and left to the database: a TRUNCATE that reaches the table only by CASCADE, `SET
session_replication_role`, replacing or dropping a trigger function, and `ALTER SEQUENCE` or
`setval` on `audit_entries_id_seq` (`tests/test_data_audit_guard_postgres.py`).

Migrations are the one legitimate schema change: `socboard.migrate.run` wraps the Alembic command
in `allow_schema_changes()`, which lifts the table and index DDL check (and only that) for the
code inside it.

Raw SQL is not refused and not parsed: not `text()`, not `connection.exec_driver_sql(...)`, not a
DBAPI cursor, and not direct access to the database file. So `DROP TRIGGER`, `UPDATE
sqlite_sequence` and `PRAGMA writable_schema` sent as raw SQL succeed; that is a recorded known
limit, not wanted behaviour. Raw-SQL UPDATE and DELETE on the table are still refused, by the
triggers. The guard matches names and the text of a `DDL(...)` construct as words; it is not a
SQL parser, so a word inside a comment or a string literal counts too.
"""

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from socboard._requirements import require

require("sqlalchemy")

from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.schema import DDL, DropIndex, ExecutableDDLElement, Table  # noqa: E402
from sqlalchemy.sql.dml import Delete, Insert, Update  # noqa: E402
from sqlalchemy.sql.elements import BindParameter, Null  # noqa: E402
from sqlalchemy.sql.selectable import AliasedReturnsRows  # noqa: E402

AUDIT_TABLE = "audit_entries"
SEQUENCE_TABLE = "sqlite_sequence"  # SQLite's table of next AUTOINCREMENT values
IMMUTABLE_MESSAGE = "Audit entries cannot be changed or removed."

# The three triggers that make the table append-only (created by migration 0001, which holds
# their SQL as literals). A `DDL(...)` naming one of them is refused like one naming the table.
TRIGGER_NAMES = ("audit_entries_no_update", "audit_entries_no_delete", "audit_entries_no_reuse_id")

# The indexes of the table (migration 0001). A `DropIndex` for one is refused even when the index
# object has no table; a later migration that adds an index must add its name here (a test checks).
INDEX_NAMES = ("ix_audit_entries_case_id",)


def _words(*names: str) -> re.Pattern:
    """`names` as whole identifiers: `$` is an identifier character in SQLite and Postgres, so `\\b` is not enough."""
    return re.compile(r"(?<![\w$])(?:" + "|".join(names) + r")(?![\w$])", re.IGNORECASE)


_WORD = _words(AUDIT_TABLE, *TRIGGER_NAMES)
_SEQUENCE_WORD = _words(SEQUENCE_TABLE)
# The names of the two pragmas that switch off the CHECK or open the schema, as words anywhere
# (so no schema prefix or quoting can hide them). No application code uses either.
_DANGEROUS_PRAGMA = re.compile(r"\b(?:ignore_check_constraints|writable_schema)\b", re.IGNORECASE)
# An execute-parameter key: a column key, or `<key>_m<n>`, the name SQLAlchemy gives the bind of
# that column in row n of a multi-row `values([...])`.
_PARAMETER_KEY = re.compile(r"(.*?)(?:_m\d+)?", re.DOTALL)  # DOTALL: any key matches; "id\n" stays "id\n"
_REPLACE = re.compile(r"\bREPLACE\b", re.IGNORECASE)
_UPDATING_CLAUSES = ("DoUpdate", "OnDuplicate")  # class-name parts of the dialect upsert clauses

_schema_changes_allowed: ContextVar[bool] = ContextVar("audit_schema_changes_allowed", default=False)


class AuditImmutableError(Exception):
    """An attempt to update, delete, overwrite or drop audit entries."""


def refuse() -> None:
    raise AuditImmutableError(IMMUTABLE_MESSAGE)


@contextmanager
def allow_schema_changes() -> Iterator[None]:
    """Let DDL on `audit_entries` through inside the block (used only by the migration runner)."""
    token = _schema_changes_allowed.set(True)
    try:
        yield
    finally:
        _schema_changes_allowed.reset(token)


def _unwrap(table):
    """Follow an alias (and any nested alias) to the table underneath."""
    for _ in range(100):  # a bound, so a cyclic object cannot loop forever
        if not isinstance(table, AliasedReturnsRows):
            break
        table = table.element
    return table


def _is_audit_name(name) -> bool:
    """Any case counts: SQLite treats table names case-insensitively, even when quoted.

    Postgres keeps a quoted name's case, so there this also refuses a separate table named, for
    example, `"Audit_Entries"`; harmless, as no such table exists (#195).
    """
    return isinstance(name, str) and name.lower() == AUDIT_TABLE


def _names_audit_table(table) -> bool:
    return _is_audit_name(getattr(_unwrap(table), "name", None))


def _names_sequence_table(table) -> bool:
    name = getattr(_unwrap(table), "name", None)
    return isinstance(name, str) and name.lower() == SEQUENCE_TABLE


def targets_audit_table(statement) -> bool:
    """True for an UPDATE or DELETE whose table (behind any alias) is `audit_entries`."""
    if not isinstance(statement, (Update, Delete)):
        return False
    table = None
    try:
        table = statement.entity_description["table"]
    except (AttributeError, KeyError, TypeError):
        table = None
    if table is None:
        table = getattr(statement, "table", None)
    return _names_audit_table(table) or _names_audit_table(getattr(statement, "table", None))


def writes_sequence_table(statement) -> bool:
    """True for an UPDATE, INSERT or DELETE on `sqlite_sequence` (a SELECT is not refused)."""
    if not isinstance(statement, (Update, Insert, Delete)):
        return False
    return _names_sequence_table(getattr(statement, "table", None))


def _key_names(key) -> set[str]:
    """Lower-cased names a values() key or parameter key can mean: a string, or a column's key and name."""
    if isinstance(key, str):
        return {key.lower()}
    return {n.lower() for n in (getattr(key, "key", None), getattr(key, "name", None)) if isinstance(n, str)}


def _parameter_mappings(parameters) -> Iterator[Mapping]:
    """Every dict inside execute parameters: a dict, or a list or tuple (nested) of them."""
    if isinstance(parameters, Mapping):
        yield parameters
    elif isinstance(parameters, (list, tuple)):
        for item in parameters:
            yield from _parameter_mappings(item)


def _values_rows(statement) -> list[Mapping]:
    """The rows of `values(...)`: `values(k=v)` is one; each row of `values([...])` is one (a tuple is in column order)."""
    rows = [statement._values or {}]
    for group in statement._multi_values:
        rows += [r if isinstance(r, Mapping) else dict(zip(statement.table.c, r)) for r in group]
    return rows


def _is_null(value) -> bool:
    """None, NULL, or the bind SQLAlchemy makes of a plain None in `values()`; never a caller's `bindparam`."""
    if isinstance(value, BindParameter):
        # `_is_crud` marks the binds `values()` makes of plain values; without it, fail closed.
        return getattr(value, "_is_crud", False) and value.value is None and value.callable is None
    return value is None or isinstance(value, Null)


def supplies_audit_id(statement, parameters=None) -> bool:
    """True for an INSERT into `audit_entries` that can supply a non-NULL `id` (#99, #107).

    Nothing is compiled. Refused: an `id` column in `from_select`; in `values(...)` (any row of a
    multi-row `values([...])`), an `id` that is not None or NULL, so any SQL expression and any
    `bindparam` whatever its value; and in any execute-parameter dict, a key `id` or `id_m<n>`
    (row n of a multi-row `values`; any case) with a value other than None. A column whose key is
    not its name (`Column("id", key="xid")`) is `id` under its key too, in `values()` and as an
    execute key, both the key itself (`xid`, also a key ending in `_m<n>`) and `xid_m<n>`.
    """
    table = getattr(statement, "table", None)
    if not isinstance(statement, Insert) or not _names_audit_table(table):
        return False
    ids = {"id", *(c.key.lower() for c in getattr(table, "c", ()) if c.name.lower() == "id")}
    if any(_key_names(name) & ids for name in statement._select_names or ()):
        return True
    for mapping in _parameter_mappings(parameters):
        for key, value in mapping.items():
            # the raw key too: a key alias can itself end in `_m<n>`
            names = {key.lower(), _PARAMETER_KEY.fullmatch(key.lower())[1]} if isinstance(key, str) else _key_names(key)
            if names & ids and value is not None:
                return True
    rows = _values_rows(statement)
    return any(_key_names(key) & ids and not _is_null(value) for row in rows for key, value in row.items())


def overwrites_audit_entry(statement) -> bool:
    """True for an INSERT into `audit_entries` with an update-on-conflict clause or a REPLACE prefix."""
    if not isinstance(statement, Insert):
        return False
    if not _names_audit_table(getattr(statement, "table", None)):
        return False
    clause = getattr(statement, "_post_values_clause", None)
    if clause is not None and any(part in type(clause).__name__ for part in _UPDATING_CLAUSES):
        return True
    for prefix in getattr(statement, "_prefixes", ()):
        clause = prefix[0] if isinstance(prefix, tuple) else prefix  # (clause, dialect name)
        if _REPLACE.search(str(getattr(clause, "text", clause))):
            return True
    return False


def _names(thing) -> Iterator[str]:
    """The names `thing` answers to: a string, or any object with a string `name`.

    After unwrapping aliases, any object with a string `name` counts: a `Table`, a lightweight
    `table("audit_entries")` (a `TableClause`, not a `Table`) or anything shaped like one. An
    object with a `table` attribute (an Index, a constraint or a column) also answers to its
    table's name.
    """
    if isinstance(thing, str):
        yield thing
        return
    thing = _unwrap(thing)
    name = getattr(thing, "name", None)
    if isinstance(name, str):
        yield name
    table = _unwrap(getattr(thing, "table", None))
    table_name = getattr(table, "name", None)
    if isinstance(table_name, str):
        yield table_name


def ddl_text(ddl) -> str:
    """The text of a `DDL(...)` to match: as SQLAlchemy renders it, or the template alone.

    SQLAlchemy computes `statement % context` (plus `table`, `schema` and `fullname` for a `Table`
    target). When that fails (a missing key, a bad format) SQLAlchemy raises before anything
    reaches the database, so only the template is matched, for the audit refusal.
    """
    template = str(ddl.statement)
    context = dict(ddl.context or {})
    if isinstance(ddl.target, Table):
        context.setdefault("table", ddl.target.name)
        context.setdefault("schema", ddl.target.schema or "")
        context.setdefault("fullname", ddl.target.fullname)
    try:
        return template % context
    except (KeyError, ValueError, TypeError, IndexError):
        return template


def ddl_is_always_refused(statement) -> bool:
    """True for a `DDL(...)` naming a dangerous pragma or `sqlite_sequence` (never lifted)."""
    if not isinstance(statement, DDL):
        return False
    text = ddl_text(statement)
    return bool(_DANGEROUS_PRAGMA.search(text) or _SEQUENCE_WORD.search(text))


def _drops_audit_index(statement) -> bool:
    """True for a `DropIndex` of an index of `audit_entries`: by name, table, or its columns' table."""
    if not isinstance(statement, DropIndex):
        return False
    index = statement.element
    name = getattr(index, "name", None)
    if isinstance(name, str) and name.lower() in INDEX_NAMES:
        return True
    # An Index on a lightweight column has no table of its own; its expressions still name one.
    return any(_names_audit_table(getattr(expr, "table", None)) for expr in getattr(index, "expressions", ()))


def ddl_targets_audit_table(statement) -> bool:
    """True for a DDL construct aimed at `audit_entries`, or a `DDL(...)` naming it or a trigger."""
    if not isinstance(statement, ExecutableDDLElement):
        return False
    for attribute in ("element", "target", "table_name", "table"):
        if any(_is_audit_name(name) for name in _names(getattr(statement, attribute, None))):
            return True
    if _drops_audit_index(statement):
        return True
    return isinstance(statement, DDL) and bool(_WORD.search(ddl_text(statement)))


def refused_statement(statement, parameters=None) -> bool:
    """True when the guard must refuse this statement.

    `parameters` are the execute parameters (a dict, or a list of dicts), needed to see an `id`
    that arrives as a parameter and not inside the statement.
    """
    if targets_audit_table(statement) or overwrites_audit_entry(statement):
        return True
    if supplies_audit_id(statement, parameters) or writes_sequence_table(statement):
        return True
    if ddl_is_always_refused(statement):
        return True
    return not _schema_changes_allowed.get() and ddl_targets_audit_table(statement)


@event.listens_for(Session, "do_orm_execute")
def _guard_orm_execute(orm_execute_state) -> None:
    if refused_statement(orm_execute_state.statement, orm_execute_state.parameters):
        refuse()


@event.listens_for(Engine, "before_execute")
def _guard_connection_execute(
    conn, clauseelement, multiparams, params, execution_options
) -> None:
    if refused_statement(clauseelement, (multiparams, params)):
        refuse()
