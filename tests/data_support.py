"""Helpers and fixtures for the data-layer tests (#11).

Each data test module imports the fixtures it needs from here (`migrated_engine`, and the two
autouse guards `isolated_database_env` and `no_default_database_file`). They live here, not in
`conftest.py`, so the older tests are untouched and a missing SQLAlchemy or Alembic fails only the
data tests, with a plain ImportError naming the package.

Every database is built in a pytest temporary directory by running the real migrations. No test
uses the default `socboard.db`.
"""

import itertools
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from socboard import database, domain, migrate
from socboard.models import Alert, Asset, AuditEntry, Case, Note, User

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)

_counter = itertools.count(1)

# Synthetic, test-only seed passwords (#13): `python -m socboard.seed` needs all three.
SEED_PASSWORDS = {
    "SOCBOARD_SEED_PASSWORD_ANALYST_A": "test-only-seed-pw-analyst-a",
    "SOCBOARD_SEED_PASSWORD_ANALYST_B": "test-only-seed-pw-analyst-b",
    "SOCBOARD_SEED_PASSWORD_LEAD": "test-only-seed-pw-lead",
}


def unique(prefix: str) -> str:
    return f"{prefix}{next(_counter)}"


def sqlite_url(path: Path) -> str:
    return "sqlite:///" + path.as_posix()


@pytest.fixture(autouse=True)
def isolated_database_env(monkeypatch, tmp_path):
    """Point SOCBOARD_DATABASE_URL at a temporary file so nothing can reach the default file."""
    monkeypatch.setenv(database.ENV_NAME, sqlite_url(tmp_path / "env-default.db"))


@pytest.fixture(autouse=True)
def no_default_database_file():
    """Fail a test if `socboard.db` (or a journal file of it) appears or changes in the repo."""
    names = ["socboard.db", "socboard.db-journal", "socboard.db-wal", "socboard.db-shm"]

    def snapshot():
        state = {}
        for name in names:
            path = REPO_ROOT / name
            state[name] = path.stat().st_mtime_ns if path.exists() else None
        return state

    before = snapshot()
    yield
    assert snapshot() == before, "a test touched the default database file in the repository"


@pytest.fixture
def db_url(tmp_path):
    return sqlite_url(tmp_path / "test.db")


@pytest.fixture
def migrated_engine(tmp_path):
    """An engine (the application's own settings) on a database built by the real migrations.

    It uses the same file as the `db_url` fixture (`tmp_path / "test.db"`), without depending on
    that fixture, so a test module needs to import only this one.
    """
    db_url = sqlite_url(tmp_path / "test.db")
    migrate.upgrade(db_url)
    engine = database.create_db_engine(db_url)
    yield engine
    engine.dispose()


# --- row helpers: Core inserts, so the tests can build rows the application never would --------


def insert_user(conn, *, username=None, role="analyst", password_hash="hash", display_name="Name"):
    row = dict(
        username=username or unique("user"),
        display_name=display_name,
        role=role,
        password_hash=password_hash,
    )
    return conn.execute(insert(User.__table__).values(**row)).inserted_primary_key[0]


def insert_asset(conn, *, name=None, criticality="High"):
    row = dict(name=name or unique("asset"), criticality=criticality)
    return conn.execute(insert(Asset.__table__).values(**row)).inserted_primary_key[0]


def alert_row(**overrides):
    row = dict(
        source="siem",
        alert_type="malware",
        asset="host-1",
        asset_criticality="High",
        title="Title",
        description="Description",
        alert_timestamp=NOW,
        ingested_at=NOW,
        severity="High",
        severity_rule="Rule text",
    )
    row.update(overrides)
    return row


def insert_alert(conn, **overrides):
    return conn.execute(insert(Alert.__table__).values(**alert_row(**overrides))).inserted_primary_key[0]


def insert_case(conn, *, alert_id=None, status="New", closing_reason=None, owner_id=None):
    if alert_id is None:
        alert_id = insert_alert(conn)
    row = dict(alert_id=alert_id, status=status, closing_reason=closing_reason, owner_id=owner_id)
    return conn.execute(insert(Case.__table__).values(**row)).inserted_primary_key[0]


def insert_note(conn, *, case_id, author_id, text="a note", created_at=NOW):
    row = dict(case_id=case_id, author_id=author_id, text=text, created_at=created_at)
    return conn.execute(insert(Note.__table__).values(**row)).inserted_primary_key[0]


def insert_audit_row(conn, *, case_id, actor_id, action="claimed", detail="detail", at=NOW):
    """A direct Core insert (not `AuditEntry(...)`), for tests that need an arbitrary row."""
    row = dict(case_id=case_id, actor_id=actor_id, action=action, detail=detail, at=at)
    return conn.execute(insert(AuditEntry.__table__).values(**row)).inserted_primary_key[0]


# --- one row builder per enumerated column, for the values test --------------------------------


def _users_role(conn, value):
    insert_user(conn, role=value)


def _assets_criticality(conn, value):
    insert_asset(conn, criticality=value)


def _alerts_severity(conn, value):
    insert_alert(conn, severity=value)


def _alerts_asset_criticality(conn, value):
    insert_alert(conn, asset_criticality=value)


def _cases_status(conn, value):
    reason = "duplicate" if value == domain.STATUS_CLOSED else None
    insert_case(conn, status=value, closing_reason=reason)


def _cases_closing_reason(conn, value):
    insert_case(conn, status=domain.STATUS_CLOSED, closing_reason=value)


def _audit_action(conn, value):
    user_id = insert_user(conn)
    case_id = insert_case(conn)
    insert_audit_row(conn, case_id=case_id, actor_id=user_id, action=value)


# (table, column, allowed values, function(conn, value) that inserts one row with that value)
ENUM_COLUMNS = [
    ("users", "role", domain.ROLES, _users_role),
    ("assets", "criticality", domain.ASSET_CRITICALITIES, _assets_criticality),
    ("alerts", "severity", domain.SEVERITIES, _alerts_severity),
    ("alerts", "asset_criticality", domain.ASSET_CRITICALITIES, _alerts_asset_criticality),
    ("cases", "status", domain.STATUSES, _cases_status),
    ("cases", "closing_reason", domain.CLOSING_REASONS, _cases_closing_reason),
    ("audit_entries", "action", domain.AUDIT_ACTIONS, _audit_action),
]


def refused_variants(allowed):
    """An unknown value and the wrong-case forms of every allowed value (none of them allowed)."""
    candidates = {"bogus", ""}
    for value in allowed:
        candidates.update({value.swapcase(), value.lower(), value.upper(), value.capitalize()})
    return sorted(candidates - set(allowed))
