"""Refuse any UPDATE or DELETE on `audit_entries` (the audit trail is append-only, spec section 5).

Three guards act at the application layer; there are no database triggers in Core (decision record
`_docs/decisions/2026-11-12T0834Z-data-model.md`):

1. Mapper events on `AuditEntry` (registered in `socboard/models.py`) raise on an ORM update or
   delete at flush time.
2. A `do_orm_execute` hook on `Session` refuses any UPDATE or DELETE statement whose table is
   `audit_entries`, in ORM or Core form.
3. One `before_execute` listener on `Engine` applies the same table check to every Core statement
   on a connection, which also covers `Session.bulk_update_mappings`.

Raw SQL through `text()` is not blocked, and neither is direct access to the database file.
"""

from socboard._requirements import require

require("sqlalchemy")

from sqlalchemy import event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.sql.dml import Delete, Update  # noqa: E402

AUDIT_TABLE = "audit_entries"
IMMUTABLE_MESSAGE = "Audit entries cannot be changed or removed."


class AuditImmutableError(Exception):
    """An attempt to update or delete an audit entry."""


def refuse() -> None:
    raise AuditImmutableError(IMMUTABLE_MESSAGE)


def targets_audit_table(statement) -> bool:
    """True for an UPDATE or DELETE whose table is `audit_entries`."""
    if not isinstance(statement, (Update, Delete)):
        return False
    table = None
    try:
        table = statement.entity_description["table"]
    except (AttributeError, KeyError, TypeError):
        table = None
    if table is None:
        table = getattr(statement, "table", None)
    return getattr(table, "name", None) == AUDIT_TABLE


@event.listens_for(Session, "do_orm_execute")
def _guard_orm_execute(orm_execute_state) -> None:
    if (orm_execute_state.is_update or orm_execute_state.is_delete) and targets_audit_table(
        orm_execute_state.statement
    ):
        refuse()


@event.listens_for(Engine, "before_execute")
def _guard_connection_execute(
    conn, clauseelement, multiparams, params, execution_options
) -> None:
    if targets_audit_table(clauseelement):
        refuse()
