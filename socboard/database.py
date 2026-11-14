"""The database URL and the engine (#11).

The URL comes from `SOCBOARD_DATABASE_URL`; the default is a SQLite file in the repository root
(git-ignored). Nothing here is wired into `create_app()`: the first route issue that reads the
database does that (#13).

Engine settings that tests read back:
- `hide_parameters=True`: SQLAlchemy errors otherwise carry the statement parameters (alert text,
  hashes) into the log through #10's 500 handler. A driver's own message is not covered.
- SQLite: foreign keys switched on for every connection the engine opens (the pragma is off by
  default), the driver's default transaction mode, and a busy timeout of 5 s. Never
  `BEGIN IMMEDIATE`. `python -m socboard.migrate` is the one place that switches the pragma off.
- Any other database (Postgres, #143): a failed connection raises `DatabaseConnectError` with fixed
  text and no chained driver error, because the driver's message names the user and the database.
"""

import os
from collections.abc import Mapping
from pathlib import Path

from socboard._requirements import require

require("sqlalchemy")

from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.engine import URL, Engine, make_url  # noqa: E402
from sqlalchemy.exc import ArgumentError  # noqa: E402

import socboard.audit_guard  # noqa: E402,F401  (registers the append-only guards)

ENV_NAME = "SOCBOARD_DATABASE_URL"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_FILE = REPO_ROOT / "socboard.db"
BUSY_TIMEOUT_SECONDS = 5

INVALID_URL_MESSAGE = "SOCBOARD_DATABASE_URL is not a valid database URL."
CONNECT_FAILED_MESSAGE = "Could not connect to the database."


class DatabaseUrlError(ValueError):
    """The database URL is not usable. The message is fixed text and never holds the value."""


class DatabaseConnectError(Exception):
    """A server database refused or failed the connection. The message is fixed text.

    The driver's own message names the user and the database (Postgres: `password authentication
    failed for user "..."`), and the 500 handler logs the exception, so it is replaced (#143).
    """


def default_url() -> URL:
    return URL.create("sqlite", database=str(DEFAULT_DATABASE_FILE))


def parse_url(raw: "str | URL") -> URL:
    if isinstance(raw, URL):
        return raw
    try:
        return make_url(raw)
    except (ArgumentError, ValueError, TypeError, AttributeError):
        raise DatabaseUrlError(INVALID_URL_MESSAGE) from None


def resolve_url(environ: Mapping[str, str] | None = None) -> URL:
    """The URL from the environment, or the default when the variable is not set."""
    source = os.environ if environ is None else environ
    if ENV_NAME not in source:
        return default_url()
    return parse_url(source[ENV_NAME])


def _enable_foreign_keys(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _connect_without_detail(dialect, connection_record, cargs, cparams):
    """Open a server connection; on failure raise fixed text with no chained driver error."""
    try:
        return dialect.loaded_dbapi.connect(*cargs, **cparams)
    except Exception:
        raise DatabaseConnectError(CONNECT_FAILED_MESSAGE) from None


def create_db_engine(url: "str | URL | None" = None) -> Engine:
    """Create the engine for `url` (default: from the environment)."""
    resolved = resolve_url() if url is None else parse_url(url)
    is_sqlite = resolved.get_backend_name() == "sqlite"
    options: dict = {"hide_parameters": True}
    if is_sqlite:
        options["connect_args"] = {"timeout": BUSY_TIMEOUT_SECONDS}
    try:
        engine = create_engine(resolved, **options)
    except (ArgumentError, ImportError, ValueError):
        raise DatabaseUrlError(INVALID_URL_MESSAGE) from None
    if is_sqlite:
        event.listen(engine, "connect", _enable_foreign_keys)
    else:
        event.listen(engine, "do_connect", _connect_without_detail)
    return engine
