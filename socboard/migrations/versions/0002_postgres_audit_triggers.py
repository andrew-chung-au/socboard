"""The append-only audit trail on Postgres: four triggers on `audit_entries` (#193, D6a).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-14

Postgres only; on SQLite this revision emits nothing (its three triggers are in 0001, which is not
edited, so a Postgres database already at 0001 gets these from here). Every statement is a literal,
as in 0001 (`tests/test_data_audit.py` refuses SQL built by string formatting).

- BEFORE UPDATE and BEFORE DELETE, per row: refuse with the texts of 0001's SQLite triggers.
- BEFORE INSERT, per row: refuse an id that already exists (`Audit entries cannot be overwritten.`).
  A BEFORE trigger fires before the unique-key check, so the caller gets this text, not a key error.
- BEFORE TRUNCATE, per statement: refuse (`Audit entries cannot be truncated.`). TRUNCATE fires no
  row trigger, and fires this one for a table reached by CASCADE too.
- All four are `ENABLE ALWAYS`, so `session_replication_role = replica` does not switch them off.
- The refusals use SQLSTATE 23000 (integrity_constraint_violation), so a caller sees the same kind
  of error as SQLite's `RAISE(ABORT, ...)` (SQLAlchemy's `IntegrityError`), and the text holds no
  row value. The functions pin `search_path` and name `public.audit_entries`, so a session's
  `search_path` cannot point them at another table.

The table owner and any superuser can still disable or drop these (Deploy record, decision 8).
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


UPGRADE = (
    "CREATE FUNCTION public.audit_entries_refuse_update() RETURNS trigger LANGUAGE plpgsql "
    "SET search_path = pg_catalog AS $$ BEGIN "
    "RAISE EXCEPTION 'Audit entries cannot be changed.' USING ERRCODE = 'integrity_constraint_violation'; "
    "END $$",
    "CREATE FUNCTION public.audit_entries_refuse_delete() RETURNS trigger LANGUAGE plpgsql "
    "SET search_path = pg_catalog AS $$ BEGIN "
    "RAISE EXCEPTION 'Audit entries cannot be removed.' USING ERRCODE = 'integrity_constraint_violation'; "
    "END $$",
    "CREATE FUNCTION public.audit_entries_refuse_reused_id() RETURNS trigger LANGUAGE plpgsql "
    "SET search_path = pg_catalog AS $$ BEGIN "
    "IF EXISTS (SELECT 1 FROM public.audit_entries WHERE id = NEW.id) THEN "
    "RAISE EXCEPTION 'Audit entries cannot be overwritten.' USING ERRCODE = 'integrity_constraint_violation'; "
    "END IF; RETURN NEW; END $$",
    "CREATE FUNCTION public.audit_entries_refuse_truncate() RETURNS trigger LANGUAGE plpgsql "
    "SET search_path = pg_catalog AS $$ BEGIN "
    "RAISE EXCEPTION 'Audit entries cannot be truncated.' USING ERRCODE = 'integrity_constraint_violation'; "
    "END $$",
    "CREATE TRIGGER audit_entries_no_update BEFORE UPDATE ON public.audit_entries "
    "FOR EACH ROW EXECUTE FUNCTION public.audit_entries_refuse_update()",
    "CREATE TRIGGER audit_entries_no_delete BEFORE DELETE ON public.audit_entries "
    "FOR EACH ROW EXECUTE FUNCTION public.audit_entries_refuse_delete()",
    "CREATE TRIGGER audit_entries_no_reuse_id BEFORE INSERT ON public.audit_entries "
    "FOR EACH ROW EXECUTE FUNCTION public.audit_entries_refuse_reused_id()",
    "CREATE TRIGGER audit_entries_no_truncate BEFORE TRUNCATE ON public.audit_entries "
    "FOR EACH STATEMENT EXECUTE FUNCTION public.audit_entries_refuse_truncate()",
    "ALTER TABLE public.audit_entries ENABLE ALWAYS TRIGGER audit_entries_no_update",
    "ALTER TABLE public.audit_entries ENABLE ALWAYS TRIGGER audit_entries_no_delete",
    "ALTER TABLE public.audit_entries ENABLE ALWAYS TRIGGER audit_entries_no_reuse_id",
    "ALTER TABLE public.audit_entries ENABLE ALWAYS TRIGGER audit_entries_no_truncate",
)

DOWNGRADE = (
    "DROP TRIGGER audit_entries_no_update ON public.audit_entries",
    "DROP TRIGGER audit_entries_no_delete ON public.audit_entries",
    "DROP TRIGGER audit_entries_no_reuse_id ON public.audit_entries",
    "DROP TRIGGER audit_entries_no_truncate ON public.audit_entries",
    "DROP FUNCTION public.audit_entries_refuse_update()",
    "DROP FUNCTION public.audit_entries_refuse_delete()",
    "DROP FUNCTION public.audit_entries_refuse_reused_id()",
    "DROP FUNCTION public.audit_entries_refuse_truncate()",
)


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        for statement in UPGRADE:
            op.execute(statement)


def downgrade() -> None:
    if _is_postgres():
        for statement in DOWNGRADE:
            op.execute(statement)
