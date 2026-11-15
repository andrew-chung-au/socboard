"""Which database the tests run on: SQLite by default, Postgres when a server URL is given (#192).

`SOCBOARD_TEST_POSTGRES_URL` names a Postgres server (`postgresql+psycopg://user:password@host:port/db`)
whose databases the tests may create and drop; the database named in the URL is only the one the
tests connect to for that (usually `postgres`). Unset, every test uses SQLite exactly as before. Set to
anything that is not a `postgresql+psycopg://` URL, the run stops at collection with a message that
holds no part of the value, because it may carry a password; it never falls back to SQLite.

This module is the one place that builds a test database URL. The rest of the tests ask for "an
empty database" or "a migrated database" and get a URL back. On Postgres each request is a new
database with a name of its own (`socboard_t_` plus 12 hex digits), dropped when the test ends
(`conftest.py`), so no test shares rows with another and a failing test leaves nothing behind.
"""

import os
import uuid
from pathlib import Path

import pytest

# SQLAlchemy is imported inside the functions: `conftest.py` imports this module, and a missing
# package must fail only the tests that need it (see the note at the top of `conftest.py`).

ENV_NAME = "SOCBOARD_TEST_POSTGRES_URL"
DRIVER = "postgresql+psycopg"
NAME_PREFIX = "socboard_t_"
INVALID_MESSAGE = (
    f"{ENV_NAME} must be a {DRIVER}:// URL of a Postgres server (the value is not shown, "
    "because it may hold a password)."
)

# URL -> the database made for one (test directory, file name) pair, so a test that names the same
# file twice gets the same database twice, as it would on SQLite.
_by_request: dict[tuple[str, str], str] = {}
# Every database this process created and has not dropped yet (the per-test ones and the template).
_created: list[str] = []
# The template databases among them: they live for the whole run.
_kept: set[str] = set()


SQLITE_MEMORY_URL = "sqlite:///:memory:"

# The reasons a test is SQLite-only, one marker each, so every module states the same reason and the
# owner issue of the Postgres version (#192 criterion 6). Found by the first Postgres run (CI job
# 132520543923): every test that carries one of these failed there, and no test that passed does.
SQLITE_TRIGGERS = pytest.mark.sqlite_only(
    "needs the SQLite audit triggers; the Postgres ones are in test_data_audit_triggers_postgres.py (#193)"
)
# The guard's own tests that fail on Postgres, by the SQLite part they need (#195: the guard on
# Postgres is tested in test_data_audit_guard_postgres.py; these files may change only their markers).
SQLITE_GUARD = pytest.mark.sqlite_only(
    "its checks read sqlite_sequence or sqlite_master, SQLite's catalogue; the guard on Postgres is in "
    "test_data_audit_guard_postgres.py (#195)"
)
SQLITE_NULL_ID = pytest.mark.sqlite_only(
    "sends an INSERT with id NULL, which SQLite fills with a new id and Postgres refuses (NOT NULL); "
    "its checks also read sqlite_sequence (#195)"
)
SQLITE_TRIGGER_SYNTAX = pytest.mark.sqlite_only(
    "creates a trigger in SQLite syntax (BEGIN ... END), which Postgres does not accept (#195)"
)
SQLITE_INTERNALS = pytest.mark.sqlite_only(
    "reads SQLite internals (sqlite_master, a PRAGMA, the driver's transaction mode or SQLite-only SQL)"
)


def sqlite_url(path: Path) -> str:
    """The URL of an SQLite file. The one place in the tests that writes the SQLite URL text.

    Tests that need a database to work with ask for `empty_database_url` or `migrated_database_url`
    instead. A test that needs an SQLite URL on purpose (it never opens a database, or tests an
    SQLite-only form) uses this and, where it opens SQLite files, carries `sqlite_only`.
    """
    return "sqlite:///" + path.as_posix()


def server_url():  # -> sqlalchemy URL | None
    """The Postgres server URL, or None when the tests run on SQLite. Raises ValueError (no value)."""
    from sqlalchemy.engine import make_url

    raw = os.environ.get(ENV_NAME)
    if raw is None:
        return None
    try:
        url = make_url(raw)
    except Exception:
        raise ValueError(INVALID_MESSAGE) from None
    if url.drivername != DRIVER or not url.host:
        raise ValueError(INVALID_MESSAGE)
    return url


def is_postgres() -> bool:
    return server_url() is not None


def _admin_engine():
    from socboard import database

    engine = database.create_db_engine(server_url())
    return engine.execution_options(isolation_level="AUTOCOMMIT")


def _quote(engine, name: str) -> str:
    return engine.dialect.identifier_preparer.quote(name)


def _url_for(name: str) -> str:
    return server_url().set(database=name).render_as_string(hide_password=False)


def _create(template: str | None = None) -> str:
    """Create one new database on the server and return its URL."""
    from sqlalchemy import text

    name = NAME_PREFIX + uuid.uuid4().hex[:12]
    engine = _admin_engine()
    try:
        with engine.connect() as conn:
            if template is None:
                conn.execute(text(f"CREATE DATABASE {_quote(engine, name)}"))
            else:
                conn.execute(text(f"CREATE DATABASE {_quote(engine, name)} TEMPLATE {_quote(engine, template)}"))
    finally:
        engine.dispose()
    _created.append(name)
    return _url_for(name)


def database_name(url: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).database


def empty_database_url(tmp_path: Path, name: str = "test.db") -> str:
    """A new, empty database; returns its URL. On SQLite it is the file `tmp_path / name`."""
    if not is_postgres():
        return sqlite_url(tmp_path / name)
    key = (str(tmp_path), name)
    if key not in _by_request:
        _by_request[key] = _create()
    return _by_request[key]


def migrated_database_url(tmp_path: Path, name: str = "test.db") -> str:
    """A new database built by the real migrations; returns its URL."""
    from socboard import migrate

    key = (str(tmp_path), name)
    fresh = is_postgres() and key not in _by_request
    url = empty_database_url(tmp_path, name)
    if not is_postgres() or fresh:
        migrate.upgrade(url)
    return url


def copy_of(template_url: str, tmp_path: Path, name: str = "app.db") -> str:
    """A copy of a prepared database: a file copy on SQLite, `CREATE DATABASE ... TEMPLATE` on Postgres."""
    if not is_postgres():
        import shutil

        from sqlalchemy.engine import make_url

        target = tmp_path / name
        shutil.copyfile(make_url(template_url).database, target)
        return sqlite_url(target)
    key = (str(tmp_path), name)
    if key not in _by_request:
        _by_request[key] = _create(template=database_name(template_url))
    return _by_request[key]


def new_template_url(tmp_path: Path, name: str = "template.db") -> str:
    """An empty database that outlives the test that first asks for it (the caller prepares it).

    It is dropped by `drop_databases(final=True)` at the end of the run.
    """
    if not is_postgres():
        return sqlite_url(tmp_path / name)
    url = _create()
    _kept.add(database_name(url))
    return url


def drop_databases(since: int = 0, *, final: bool = False) -> int:
    """Drop the databases created after position `since`; returns how many.

    The template databases are dropped only when `final` is true.
    """
    from sqlalchemy import text

    names = [n for n in _created[since:] if final or n not in _kept]
    if not names:
        return 0
    engine = _admin_engine()
    try:
        with engine.connect() as conn:
            for name in names:
                conn.execute(text(f"DROP DATABASE IF EXISTS {_quote(engine, name)} WITH (FORCE)"))
    finally:
        engine.dispose()
    for name in names:
        _created.remove(name)
        _kept.discard(name)
    for key in [k for k, v in _by_request.items() if database_name(v) in names]:
        del _by_request[key]
    return len(names)


def created_count() -> int:
    return len(_created)


def created_names() -> list[str]:
    return list(_created)


def listed_databases() -> list[str]:
    """The names of every database on the server that this module's naming scheme made."""
    from sqlalchemy import text

    engine = _admin_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT datname FROM pg_database WHERE starts_with(datname, :p) ORDER BY datname"),
                {"p": NAME_PREFIX},
            )
            return [row[0] for row in rows]
    finally:
        engine.dispose()
