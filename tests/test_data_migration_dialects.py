"""Migration 0001 on each dialect (#143), compiled to SQL text with no database server.

Alembic's offline mode renders each statement for a dialect without connecting, so these tests run
in the default (SQLite) test run. They show that the SQLite-only parts of 0001 (the three audit
triggers and AUTOINCREMENT) are emitted on SQLite and not on Postgres, and that the Postgres ids are
64-bit and generated. Running the migration on a real Postgres is the CI Postgres job's evidence.
"""

import importlib.util
import io

from alembic.migration import MigrationContext
from alembic.operations import Operations

from socboard import migrate

MIGRATION_0001 = migrate.SCRIPT_LOCATION / "versions" / "0001_initial_schema.py"
TRIGGER_NAMES = ("audit_entries_no_update", "audit_entries_no_delete", "audit_entries_no_reuse_id")


def load_0001():
    spec = importlib.util.spec_from_file_location("migration_0001_under_test", MIGRATION_0001)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rendered_sql(dialect_name: str, step: str) -> str:
    """The SQL text 0001's `upgrade` or `downgrade` emits for `dialect_name`."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name=dialect_name, opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        getattr(load_0001(), step)()
    return buffer.getvalue()


def test_sqlite_upgrade_creates_the_three_triggers_and_autoincrement():
    sql = rendered_sql("sqlite", "upgrade")
    for name in TRIGGER_NAMES:
        assert f"CREATE TRIGGER {name} " in sql
    assert "AUTOINCREMENT" in sql


def test_sqlite_downgrade_drops_the_three_triggers():
    sql = rendered_sql("sqlite", "downgrade")
    for name in TRIGGER_NAMES:
        assert f"DROP TRIGGER {name}" in sql


def test_postgres_upgrade_emits_no_sqlite_trigger_and_no_autoincrement():
    sql = rendered_sql("postgresql", "upgrade")
    assert "TRIGGER" not in sql.upper()
    assert "RAISE(ABORT" not in sql.upper()
    assert "AUTOINCREMENT" not in sql.upper()


def test_postgres_downgrade_drops_no_trigger():
    sql = rendered_sql("postgresql", "downgrade")
    assert "TRIGGER" not in sql.upper()
    assert "DROP TABLE audit_entries" in sql


def test_postgres_ids_are_64_bit_and_generated():
    sql = rendered_sql("postgresql", "upgrade")
    create_audit = sql[sql.index("CREATE TABLE audit_entries") :].split(";", 1)[0]
    assert "id BIGSERIAL NOT NULL" in create_audit
    for table in ("users", "assets", "alerts", "cases", "notes"):
        create = sql[sql.index(f"CREATE TABLE {table} ") :].split(";", 1)[0]
        assert "id BIGSERIAL NOT NULL" in create, table


def test_postgres_upgrade_keeps_every_table_constraint_and_index():
    sql = rendered_sql("postgresql", "upgrade")
    for name in (
        "ck_users_role",
        "uq_users_username",
        "ck_assets_criticality",
        "ck_alerts_severity",
        "ck_alerts_asset_criticality",
        "ck_cases_status",
        "ck_cases_closing_reason_values",
        "ck_cases_closed_has_reason",
        "ck_cases_open_has_no_reason",
        "fk_cases_alert_id_alerts",
        "fk_cases_owner_id_users",
        "uq_cases_alert_id",
        "fk_notes_case_id_cases",
        "fk_notes_author_id_users",
        "ix_notes_case_id",
        "ck_audit_entries_id_positive",
        "ck_audit_entries_action",
        "ck_audit_entries_actor_null_only_ingested",
        "fk_audit_entries_case_id_cases",
        "fk_audit_entries_actor_id_users",
        "ix_audit_entries_case_id",
    ):
        assert name in sql, name
