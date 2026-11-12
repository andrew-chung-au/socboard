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
  (what `append_audit` does) and `ON CONFLICT DO NOTHING` pass;
- a DDL construct aimed at the table (`DropTable`, `CreateTable`, an index or constraint on it,
  an Alembic `ALTER TABLE` construct, or `DDL(...).against(table)`): any object with a string
  `name` after unwrapping aliases counts, so a lightweight `table("audit_entries")` is covered
  too; and a `DDL("...")` whose text names `audit_entries` or one of the three trigger names as a
  whole word.

Migrations are the one legitimate schema change: `socboard.migrate.run` wraps the Alembic command
in `allow_schema_changes()`, which lifts the DDL check (and only that) for the code inside it.

Raw-SQL DDL is not refused: not `text()`, not `connection.exec_driver_sql(...)`, not a DBAPI
cursor, and not direct access to the database file. So `DROP TRIGGER` sent as raw SQL succeeds;
that is a recorded known limit, not wanted behaviour. Raw-SQL UPDATE and DELETE are still
refused, by the triggers. The module does not try to parse raw SQL. The one string inspected is
the text of a `DDL(...)` construct, which is a plain whole-word match, not a parse.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from socboard._requirements import require

require("sqlalchemy")

from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.schema import DDL, ExecutableDDLElement  # noqa: E402
from sqlalchemy.sql.dml import Delete, Insert, Update  # noqa: E402
from sqlalchemy.sql.selectable import AliasedReturnsRows  # noqa: E402

AUDIT_TABLE = "audit_entries"
IMMUTABLE_MESSAGE = "Audit entries cannot be changed or removed."

# The three triggers that make the table append-only (created by migration 0001, which holds
# their SQL as literals). A `DDL(...)` naming one of them is refused like one naming the table.
TRIGGER_NAMES = ("audit_entries_no_update", "audit_entries_no_delete", "audit_entries_no_reuse_id")

_WORD = re.compile(
    r"\b(?:" + "|".join((AUDIT_TABLE, *TRIGGER_NAMES)) + r")\b", re.IGNORECASE
)
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


def ddl_targets_audit_table(statement) -> bool:
    """True for a DDL construct aimed at `audit_entries`, or a `DDL(...)` naming it or a trigger."""
    if not isinstance(statement, ExecutableDDLElement):
        return False
    for attribute in ("element", "target", "table_name", "table"):
        if any(_is_audit_name(name) for name in _names(getattr(statement, attribute, None))):
            return True
    if isinstance(statement, DDL) and _WORD.search(str(statement.statement)):
        return True
    return False


def refused_statement(statement) -> bool:
    """True when the guard must refuse this statement."""
    if targets_audit_table(statement) or overwrites_audit_entry(statement):
        return True
    return not _schema_changes_allowed.get() and ddl_targets_audit_table(statement)


@event.listens_for(Session, "do_orm_execute")
def _guard_orm_execute(orm_execute_state) -> None:
    if refused_statement(orm_execute_state.statement):
        refuse()


@event.listens_for(Engine, "before_execute")
def _guard_connection_execute(
    conn, clauseelement, multiparams, params, execution_options
) -> None:
    if refused_statement(clauseelement):
        refuse()
