"""Initial schema: users, assets, alerts, cases, notes, audit entries (#11).

Revision ID: 0001
Revises:
Create Date: 2026-11-12

Everything here is a literal: names, types and the CHECK text. This file never imports
`socboard.domain` or `socboard.models`, so a later change to either cannot change what this
migration does. `tests/test_data_schema.py` inserts every allowed value and a set of refused ones
into the migrated database, because Alembic's autogenerate does not compare CHECK constraints.
The three SQLite triggers on `audit_entries` are literals too; `tests/test_data_audit_triggers.py`
and `tests/test_data_migrations.py` check them, because autogenerate does not read triggers.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


AUDIT_TRIGGERS = {
    "audit_entries_no_update": (
        "CREATE TRIGGER audit_entries_no_update BEFORE UPDATE ON audit_entries "
        "BEGIN SELECT RAISE(ABORT, 'Audit entries cannot be changed.'); END"
    ),
    "audit_entries_no_delete": (
        "CREATE TRIGGER audit_entries_no_delete BEFORE DELETE ON audit_entries "
        "BEGIN SELECT RAISE(ABORT, 'Audit entries cannot be removed.'); END"
    ),
    "audit_entries_no_reuse_id": (
        "CREATE TRIGGER audit_entries_no_reuse_id BEFORE INSERT ON audit_entries "
        "WHEN NEW.id IS NOT NULL AND EXISTS (SELECT 1 FROM audit_entries WHERE id = NEW.id) "
        "BEGIN SELECT RAISE(ABORT, 'Audit entries cannot be overwritten.'); END"
    ),
}


# One literal DROP per trigger, in the same order as above.
AUDIT_TRIGGER_DROPS = (
    "DROP TRIGGER audit_entries_no_update",
    "DROP TRIGGER audit_entries_no_delete",
    "DROP TRIGGER audit_entries_no_reuse_id",
)


def _id():
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", _id(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.CheckConstraint("role IN ('analyst', 'lead')", name=op.f("ck_users_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "assets",
        sa.Column("id", _id(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("criticality", sa.String(), nullable=False),
        sa.CheckConstraint(
            "criticality IN ('Low', 'Medium', 'High', 'Critical')",
            name=op.f("ck_assets_criticality"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
        sa.UniqueConstraint("name", name=op.f("uq_assets_name")),
    )
    op.create_table(
        "alerts",
        sa.Column("id", _id(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("alert_type", sa.Text(), nullable=False),
        sa.Column("asset", sa.Text(), nullable=False),
        sa.Column("asset_criticality", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("alert_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("severity_rule", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "severity IN ('Low', 'Medium', 'High', 'Critical')",
            name=op.f("ck_alerts_severity"),
        ),
        sa.CheckConstraint(
            "asset_criticality IN ('Low', 'Medium', 'High', 'Critical')",
            name=op.f("ck_alerts_asset_criticality"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alerts")),
    )
    op.create_table(
        "cases",
        sa.Column("id", _id(), nullable=False),
        sa.Column("alert_id", _id(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("closing_reason", sa.String(), nullable=True),
        sa.Column("owner_id", _id(), nullable=True),
        sa.CheckConstraint(
            "status IN ('New', 'Investigating', 'Closed')",
            name=op.f("ck_cases_status"),
        ),
        sa.CheckConstraint(
            "closing_reason IS NULL OR closing_reason IN "
            "('true_positive', 'false_positive', 'duplicate')",
            name=op.f("ck_cases_closing_reason_values"),
        ),
        sa.CheckConstraint(
            "status <> 'Closed' OR closing_reason IS NOT NULL",
            name=op.f("ck_cases_closed_has_reason"),
        ),
        sa.CheckConstraint(
            "status = 'Closed' OR closing_reason IS NULL",
            name=op.f("ck_cases_open_has_no_reason"),
        ),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"], name=op.f("fk_cases_alert_id_alerts")),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_cases_owner_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
        sa.UniqueConstraint("alert_id", name=op.f("uq_cases_alert_id")),
    )
    op.create_table(
        "notes",
        sa.Column("id", _id(), nullable=False),
        sa.Column("case_id", _id(), nullable=False),
        sa.Column("author_id", _id(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], name=op.f("fk_notes_case_id_cases")),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], name=op.f("fk_notes_author_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notes")),
    )
    op.create_index(op.f("ix_notes_case_id"), "notes", ["case_id"], unique=False)
    op.create_table(
        "audit_entries",
        sa.Column("id", _id(), nullable=False),
        sa.Column("case_id", _id(), nullable=False),
        sa.Column("actor_id", _id(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('ingested', 'claimed', 'status_changed', 'note_added', 'reassigned')",
            name=op.f("ck_audit_entries_action"),
        ),
        sa.CheckConstraint(
            "actor_id IS NOT NULL OR action = 'ingested'",
            name=op.f("ck_audit_entries_actor_null_only_ingested"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_audit_entries_case_id_cases")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_audit_entries_actor_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_entries")),
        sqlite_autoincrement=True,
    )
    op.create_index(op.f("ix_audit_entries_case_id"), "audit_entries", ["case_id"], unique=False)
    # The audit trail is append-only in the database (decision record, option B). SQLite's
    # triggers; Postgres gets equivalents in the Deploy phase. Each message is fixed text with
    # no value in it. Trigger (c) fires before the insert is attempted, so it also blocks
    # INSERT OR REPLACE, REPLACE and ON CONFLICT DO UPDATE, whether or not PRAGMA
    # recursive_triggers is on.
    for statement in AUDIT_TRIGGERS.values():
        op.execute(statement)


def downgrade() -> None:
    # The triggers go before the table. Literal statements: no query is built by formatting.
    for statement in AUDIT_TRIGGER_DROPS:
        op.execute(statement)
    op.drop_index(op.f("ix_audit_entries_case_id"), table_name="audit_entries")
    op.drop_table("audit_entries")
    op.drop_index(op.f("ix_notes_case_id"), table_name="notes")
    op.drop_table("notes")
    op.drop_table("cases")
    op.drop_table("alerts")
    op.drop_table("assets")
    op.drop_table("users")
