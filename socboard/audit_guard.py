"""Refuse any change to `audit_entries` (the audit trail is append-only, spec section 5).

Three guards act at the application layer; there are no database triggers in Core (decision record
`_docs/decisions/2026-11-12T0834Z-data-model.md`):

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
  an Alembic `ALTER TABLE` construct, or `DDL(...).against(table)`), and a `DDL("...")` whose text
  names `audit_entries` as a whole word.

Migrations are the one legitimate schema change: `socboard.migrate.run` wraps the Alembic command
in `allow_schema_changes()`, which lifts the DDL check (and only that) for the code inside it.

Raw SQL is not blocked: not `text()`, not `connection.exec_driver_sql(...)` (also raw SQL), and
not direct access to the database file. The record names these as the only ways around the guard,
and the module does not try to parse raw SQL. The one string inspected is the text of a `DDL(...)`
construct, which is a plain whole-word match, not a parse.
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
from sqlalchemy.schema import DDL, ExecutableDDLElement, Table  # noqa: E402
from sqlalchemy.sql.dml import Delete, Insert, Update  # noqa: E402
from sqlalchemy.sql.selectable import AliasedReturnsRows  # noqa: E402

AUDIT_TABLE = "audit_entries"
IMMUTABLE_MESSAGE = "Audit entries cannot be changed or removed."

_WORD = re.compile(r"\b" + AUDIT_TABLE + r"\b", re.IGNORECASE)
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


def _table_name(thing) -> str | None:
    if isinstance(thing, str):
        return thing
    thing = _unwrap(thing)
    if isinstance(thing, Table):
        return thing.name
    table = _unwrap(getattr(thing, "table", None))  # an Index, a constraint or a column
    return table.name if isinstance(table, Table) else None


def ddl_targets_audit_table(statement) -> bool:
    """True for a DDL construct aimed at `audit_entries`."""
    if not isinstance(statement, ExecutableDDLElement):
        return False
    for attribute in ("element", "target", "table_name", "table"):
        if _is_audit_name(_table_name(getattr(statement, attribute, None))):
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
