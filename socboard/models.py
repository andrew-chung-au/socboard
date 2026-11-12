"""SQLAlchemy 2.x models for users, assets, alerts, cases, notes and audit entries (#11).

Names, values and the id rule follow `openapi.yaml`. Every primary key and every foreign-key
column is a 64-bit integer (`ID_TYPE`; plain INTEGER on SQLite so the database assigns it). Every
constraint and index gets its name from `NAMING_CONVENTION`. No foreign key cascades. The
models carry no relationships yet: the issues that read the tables add what they need.

The values of the enumerated columns come from `socboard/domain.py`; Alembic does not compare
CHECK constraints, so the migration holds the same text as literals and the tests prove it.
"""

from datetime import datetime

from socboard._requirements import require

require("sqlalchemy")

from sqlalchemy import (  # noqa: E402
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column  # noqa: E402

from socboard import audit_guard  # noqa: E402
from socboard.db_types import UtcDateTime  # noqa: E402
from socboard.domain import (  # noqa: E402
    ASSET_CRITICALITIES,
    AUDIT_ACTIONS,
    CLOSING_REASONS,
    ROLES,
    SEVERITIES,
    STATUS_CLOSED,
    STATUSES,
    in_check,
    sql_literal,
)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# A 64-bit key on every database; INTEGER on SQLite, where only INTEGER PRIMARY KEY is assigned
# by the database. Any id `from_public_id` accepts fits the column.
ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


metadata = Base.metadata

_CLOSED = sql_literal(STATUS_CLOSED)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username"),
        CheckConstraint(in_check("role", ROLES), name="role"),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    # The hashing algorithm is chosen in #13. Never part of `repr` and never returned by the API.
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, username={self.username!r}, role={self.role!r})"


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint(in_check("criticality", ASSET_CRITICALITIES), name="criticality"),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    criticality: Mapped[str] = mapped_column(String, nullable=False)

    def __repr__(self) -> str:
        return f"Asset(id={self.id!r}, name={self.name!r})"


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(in_check("severity", SEVERITIES), name="severity"),
        CheckConstraint(in_check("asset_criticality", ASSET_CRITICALITIES), name="asset_criticality"),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    # The ingest field `type`.
    alert_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Plain text, not a foreign key: an alert may name an asset that is not in the inventory
    # (spec section 4: an unknown asset counts as Medium).
    asset: Mapped[str] = mapped_column(Text, nullable=False)
    # The criticality in force when the alert was scored, so a case keeps showing what its
    # severity was based on.
    asset_criticality: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    alert_timestamp: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    # Stored only. The scoring rules are #12.
    severity: Mapped[str] = mapped_column(String, nullable=False)
    severity_rule: Mapped[str] = mapped_column(Text, nullable=False)


class Case(Base):
    __tablename__ = "cases"
    # Choosing the closing reason, refusing a close without one (422 `reason_required`) and the
    # status transitions are #17. The two CHECKs below only keep the stored state consistent:
    # a reason is present if and only if the status is Closed.
    # The owner is `owner_id` and nowhere else (no assignment table). A claim is one conditional
    # UPDATE on this column (decision record, implemented and tested in #16).
    __table_args__ = (
        UniqueConstraint("alert_id"),
        CheckConstraint(in_check("status", STATUSES), name="status"),
        CheckConstraint(
            "closing_reason IS NULL OR " + in_check("closing_reason", CLOSING_REASONS),
            name="closing_reason_values",
        ),
        CheckConstraint(
            "status <> " + _CLOSED + " OR closing_reason IS NOT NULL",
            name="closed_has_reason",
        ),
        CheckConstraint(
            "status = " + _CLOSED + " OR closing_reason IS NULL",
            name="open_has_no_reason",
        ),
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ID_TYPE, ForeignKey("alerts.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    closing_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ID_TYPE, ForeignKey("users.id"), nullable=True)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    case_id: Mapped[int] = mapped_column(ID_TYPE, ForeignKey("cases.id"), nullable=False, index=True)
    author_id: Mapped[int] = mapped_column(ID_TYPE, ForeignKey("users.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class AuditEntry(Base):
    """Created only by `socboard.audit.append_audit`; never updated or deleted."""

    __tablename__ = "audit_entries"
    __table_args__ = (
        # Keeps the id-reuse trigger usable: SQLite gives NEW.id the value -1 in a BEFORE INSERT
        # trigger when no id is supplied, and the trigger only asks whether a row with id = NEW.id
        # exists. One row with id -1 would make every later plain insert fail. Only this table.
        CheckConstraint("id >= 1", name="id_positive"),
        CheckConstraint(in_check("action", AUDIT_ACTIONS), name="action"),
        # A null actor is the contract's actor "system", allowed only for `ingested`.
        CheckConstraint(
            "actor_id IS NOT NULL OR action = " + sql_literal("ingested"),
            name="actor_null_only_ingested",
        ),
        # An id is never reused.
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True)
    case_id: Mapped[int] = mapped_column(ID_TYPE, ForeignKey("cases.id"), nullable=False, index=True)
    # Null means the actor "system". There is no `system` row in `users`.
    actor_id: Mapped[int | None] = mapped_column(ID_TYPE, ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


@event.listens_for(AuditEntry, "before_update")
def _refuse_audit_update(mapper, connection, target) -> None:
    audit_guard.refuse()


@event.listens_for(AuditEntry, "before_delete")
def _refuse_audit_delete(mapper, connection, target) -> None:
    audit_guard.refuse()
