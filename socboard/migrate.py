"""Apply the migrations: `python -m socboard.migrate` (run by `make migrate`).

The database is `SOCBOARD_DATABASE_URL` (default: `socboard.db` in the repository root). A value
that is not a valid database URL exits 2 with fixed text that does not print the value (a URL can
hold a password). A second run changes nothing.

Alembic is configured in code (no `alembic.ini`); the environment is `socboard/migrations/env.py`.

SQLite foreign keys: the engine switches the pragma on for every connection, and this module is
the one exception. SQLite's documented way to change a table that other tables reference is to
create a new table, copy, drop the old one and rename; with the pragma on, the DROP is refused
while child rows exist. So the migration connection switches the pragma off before the migration
runs, and `PRAGMA foreign_key_check` runs afterwards; any row it returns is a failure (exit 1).

The functions import SQLAlchemy and Alembic when called, so a missing package gives a plain
message here, not a traceback.
"""

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

SCRIPT_LOCATION = Path(__file__).resolve().parent / "migrations"

MISSING_PACKAGE_EXIT = 1
INVALID_URL_EXIT = 2


class MigrationError(Exception):
    """A migration run failed. The message is fixed text."""


def _load():
    from socboard._requirements import require

    require("sqlalchemy")
    require("alembic")
    from alembic import command
    from alembic.config import Config

    from socboard import database

    return database, command, Config


def make_config(script_location: Path | str | None = None, connection=None):
    """Alembic's configuration, built in code. `connection` is handed to `env.py`."""
    _database, _command, Config = _load()
    config = Config()
    config.set_main_option("script_location", str(script_location or SCRIPT_LOCATION))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def run(url, action: Callable, *, script_location: Path | str | None = None) -> None:
    """Run `action(config)` (an Alembic command) on a fresh connection to `url`."""
    database, _command, _Config = _load()
    engine = database.create_db_engine(url)
    try:
        with engine.connect() as connection:
            is_sqlite = connection.dialect.name == "sqlite"
            if is_sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
                state = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
                connection.commit()
                if state != 0:
                    raise MigrationError("Could not switch SQLite foreign keys off for the migration.")
            action(make_config(script_location, connection))
            connection.commit()
            if is_sqlite:
                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                connection.rollback()
                if violations:
                    raise MigrationError("The migration left foreign key violations.")
    finally:
        engine.dispose()


def upgrade(url, revision: str = "head", *, script_location: Path | str | None = None) -> None:
    _database, command, _Config = _load()
    run(url, lambda config: command.upgrade(config, revision), script_location=script_location)


def downgrade(url, revision: str, *, script_location: Path | str | None = None) -> None:
    _database, command, _Config = _load()
    run(url, lambda config: command.downgrade(config, revision), script_location=script_location)


def main(environ: Mapping[str, str] | None = None) -> int:
    source = os.environ if environ is None else environ
    try:
        database, _command, _Config = _load()
    except ImportError as error:
        print(f"socboard.migrate: {error}", file=sys.stderr)
        return MISSING_PACKAGE_EXIT
    try:
        url = database.resolve_url(source)
        upgrade(url)
    except database.DatabaseUrlError as error:
        print(f"socboard.migrate: {error}", file=sys.stderr)
        return INVALID_URL_EXIT
    except MigrationError as error:
        print(f"socboard.migrate: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # fixed text: the class name only, never the message
        print(f"socboard.migrate: the migration failed ({type(error).__name__})", file=sys.stderr)
        return 1
    print("socboard.migrate: the database is at the latest migration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
