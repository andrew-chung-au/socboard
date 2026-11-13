"""Refuse any change to `audit_entries` (the audit trail is append-only, spec section 5).

The database is the first layer: migration 0001 creates three SQLite triggers on `audit_entries`
that abort any UPDATE, any DELETE and any INSERT that reuses an id. This module is the second
layer, and the only guard against DDL on the table (the triggers cannot stop a DROP) (decision
record `_docs/decisions/2026-11-12T0834Z-data-model.md`). Three guards act at the application
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
- an INSERT that supplies a non-NULL `id` (#99): the id is assigned by the database, and an id
  near 2^63-1 would make every later `append_audit` fail. This covers `values(id=...)`, a row of a
  multi-row `values([...])`, the execute parameters (a dict or a list of dicts, on a connection or
  a `Session`, under any name the compiler gives the bind: `id`, `id_m<n>`, `param_1`, the key of
  a `bindparam`), the `id` column of `from_select`, and an ORM `AuditEntry(id=...)`. The statement
  is compiled with the connection's dialect and the execute parameters, and it is refused unless
  every value bound for `id`, in every parameter row, is None; an expression in the `id` position
  (other than NULL) and a form that cannot be compiled are refused. An `id` of `None` or no `id`
  passes. `allow_schema_changes()` does not lift this;
- a DDL construct aimed at the table (`DropTable`, `CreateTable`, an index or constraint on it,
  an Alembic `ALTER TABLE` construct, or `DDL(...).against(table)`): any object with a string
  `name` after unwrapping aliases counts, so a lightweight `table("audit_entries")` is covered
  too; `DropIndex` for the index of the table, by its name (`INDEX_NAMES`) or by the table it is
  built on; and a `DDL("...")` whose rendered text (the template after its `context`
  substitution; the template and the string context values when it cannot be rendered) names
  `audit_entries` or one of the three trigger names as a whole word;
- a `DDL(...)` whose text contains `ignore_check_constraints` or `writable_schema` as a word
  (which would switch off the CHECK or allow edits of the schema; any spelling, quoting or schema
  prefix), or that starts a statement with `ATTACH` or `DETACH` (an alias to hide the pragma
  behind), and any UPDATE, INSERT, DELETE or `DDL(...)` naming `sqlite_sequence` (which holds the
  next audit id). None of these is lifted by `allow_schema_changes()`. A SELECT from
  `sqlite_sequence` passes. The text of a `DDL(...)` is matched both as rendered and with SQL
  comments stripped, so a comment cannot split a name.
  Not covered: a custom `ExecutableDDLElement` with `@compiles` that emits `DROP TRIGGER`, and
  `CreateIndex` of a new index on a lightweight `audit_entries` column.

Migrations are the one legitimate schema change: `socboard.migrate.run` wraps the Alembic command
in `allow_schema_changes()`, which lifts the table and index DDL check (and only that) for the
code inside it.

Raw SQL is not refused and not parsed: not `text()`, not `connection.exec_driver_sql(...)`, not a
DBAPI cursor, and not direct access to the database file. So `DROP TRIGGER`, `UPDATE
sqlite_sequence` and `PRAGMA writable_schema` sent as raw SQL succeed; that is a recorded known
limit, not wanted behaviour. Raw-SQL UPDATE and DELETE on the table are still refused, by the
triggers. The guard matches names and the rendered text of a `DDL(...)` construct as whole
words; it is not a SQL parser.
"""

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

from socboard._requirements import require

require("sqlalchemy")

from sqlalchemy import event  # noqa: E402
from sqlalchemy.dialects import sqlite  # noqa: E402
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

_WORD = re.compile(
    r"\b(?:" + "|".join((AUDIT_TABLE, *TRIGGER_NAMES)) + r")\b", re.IGNORECASE
)
_SEQUENCE_WORD = re.compile(r"\b" + SEQUENCE_TABLE + r"\b", re.IGNORECASE)
# The names of the two pragmas that switch off the CHECK or open the schema, as words anywhere
# (so no schema prefix or quoting can hide them); and ATTACH / DETACH at the start of a statement
# (an attached database gets an alias the pragma could hide behind). No application code uses any.
_DANGEROUS_PRAGMA = re.compile(r"\b(?:ignore_check_constraints|writable_schema)\b", re.IGNORECASE)
_ATTACH = re.compile(r"(?:\A|;)\s*(?:ATTACH|DETACH)\b", re.IGNORECASE)
# An SQL comment: `/* ... */` (an unterminated one runs to the end) or `-- ...` to the line end.
_COMMENT = re.compile(r"/\*.*?(?:\*/|\Z)|--[^\n]*", re.DOTALL)
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
    """SQLite treats table names case-insensitively, even when quoted."""
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


def supplies_audit_id(statement, parameters=None, dialect=None) -> bool:
    """True for an INSERT into `audit_entries` that supplies a non-NULL `id` in any form (#99).

    Refused without compiling: an `id` in `from_select`; an `id` key in an execute dict with a
    value other than None; an `id` in `values(...)` that is a Python literal other than None or a
    SQL expression other than NULL or a bound parameter.

    The rest is asked of SQLAlchemy. The statement is compiled for `dialect` (the connection's;
    SQLite when None) the way `Connection.execute` does: `column_keys` from the first parameter
    dict, `for_executemany` for more than one. The compiler names the bind of each column (`id`, or
    `id_m<n>` in row n of a multi-row `values()`) and of a `bindparam` in `values()` (its own key,
    or an anonymous `param_1`); `construct_params` gives the value each bind gets from every
    execute dict, with the same lookup as at execution. Refused unless all of them are None.
    A form that cannot be compiled or resolved (the execute would fail too) is refused.
    """
    if not isinstance(statement, Insert) or not _names_audit_table(getattr(statement, "table", None)):
        return False
    if any("id" in _key_names(name) for name in statement._select_names or ()):
        return True
    mappings = [m for m in _parameter_mappings(parameters) if m]
    # SQLAlchemy reads only the first dict's keys, so an `id` in a later dict needs its own check.
    if any("id" in _key_names(key) and value is not None for m in mappings for key, value in m.items()):
        return True
    rows = _values_rows(statement)
    slots = [value for row in rows for key, value in row.items() if "id" in _key_names(key)]
    if any(not (value is None or isinstance(value, (Null, BindParameter))) for value in slots):
        return True
    names = {"id", *(f"id_m{n}" for n in range(len(rows)))}
    keys = {value.key for value in slots if isinstance(value, BindParameter)}
    try:
        compiled = statement.compile(
            dialect=dialect or sqlite.dialect(),
            column_keys=sorted(mappings[0]) if mappings else [],
            for_executemany=len(mappings) > 1,
        )
        id_binds = {name for bind, name in compiled.bind_names.items() if name in names or bind.key in keys}
        # _check=False: a bind with no value stays None, as the statement alone says (SQLAlchemy itself
        # raises later for a missing required value).
        return any(
            value is not None
            for row in mappings or [{}]
            for name, value in compiled.construct_params(row, escape_names=False, _check=False).items()
            if name in id_binds
        )
    except Exception:  # fail closed: a form that cannot be compiled or resolved here is refused
        return True


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


def ddl_texts(ddl) -> list[str]:
    """The text(s) of a `DDL(...)` to match: the rendered text, as SQLAlchemy compiles it.

    SQLAlchemy computes `statement % context` (plus `table`, `schema` and `fullname` for a `Table`
    target). When that fails (a missing key, a bad format) the template and every string value of
    the context are matched instead.
    """
    template = str(ddl.statement)
    context = dict(ddl.context or {})
    if isinstance(ddl.target, Table):
        context.setdefault("table", ddl.target.name)
        context.setdefault("schema", ddl.target.schema or "")
        context.setdefault("fullname", ddl.target.fullname)
    try:
        texts = [template % context]
    except (KeyError, ValueError, TypeError, IndexError):
        texts = [template, *(value for value in context.values() if isinstance(value, str))]
    # SQLite reads a comment as whitespace, so match the comment-free text too. The raw text stays
    # in the list: a quoted `'--'` or `'/*'` is not a comment, and stripping it could hide a name.
    return [*texts, *(_strip_comments(text) for text in texts)]


def _strip_comments(text: str) -> str:
    """Replace `/* ... */` and `-- ...` comments with a space, then collapse whitespace."""
    return " ".join(_COMMENT.sub(" ", text).split())


def ddl_is_always_refused(statement) -> bool:
    """True for a `DDL(...)` naming a dangerous pragma or `sqlite_sequence`, or an ATTACH/DETACH (never lifted)."""
    if not isinstance(statement, DDL):
        return False
    return any(
        pattern.search(text) for text in ddl_texts(statement) for pattern in (_DANGEROUS_PRAGMA, _ATTACH, _SEQUENCE_WORD)
    )


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
    return isinstance(statement, DDL) and any(_WORD.search(text) for text in ddl_texts(statement))


def refused_statement(statement, parameters=None, dialect=None) -> bool:
    """True when the guard must refuse this statement.

    `parameters` are the execute parameters (a dict, or a list of dicts), needed to see an `id`
    that arrives as a parameter and not inside the statement. `dialect` is the one the statement
    is compiled for to resolve them (the connection's; SQLite when None).
    """
    if targets_audit_table(statement) or overwrites_audit_entry(statement):
        return True
    if supplies_audit_id(statement, parameters, dialect) or writes_sequence_table(statement):
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
    if refused_statement(clauseelement, (multiparams, params), conn.dialect):
        refuse()
