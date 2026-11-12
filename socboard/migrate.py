"""Apply the migrations: `python -m socboard.migrate` (run by `make migrate`).

The database is `SOCBOARD_DATABASE_URL` (default: `socboard.db` in the repository root). A value
that is not a valid database URL exits 2 with fixed text that does not print the value (a URL can
hold a password). A second run changes nothing.

`python -m socboard.migrate new NAME` (run by `make migration-new`) generates a migration file from
the model changes; NAME is letters, digits, `_` and `-` only, and a bad name creates nothing.

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
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

SCRIPT_LOCATION = Path(__file__).resolve().parent / "migrations"

MISSING_PACKAGE_EXIT = 1
INVALID_URL_EXIT = 2  # also the exit code for a bad command line

NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
USAGE = "usage: python -m socboard.migrate [new NAME]  (NAME: letters, digits, _ and - only)"


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
            from socboard.audit_guard import allow_schema_changes

            with allow_schema_changes():  # a migration may create or drop audit_entries
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


def is_valid_name(name: str) -> bool:
    """A migration name: ASCII letters, digits, `_` and `-` only (no `$`, no spaces)."""
    return NAME_PATTERN.fullmatch(name) is not None


def new_revision(url, message: str, *, script_location: Path | str | None = None) -> None:
    """Generate a migration file from the model changes.

    Alembic compares the models with the database, so the database is brought to the latest
    migration first.
    """
    _database, command, _Config = _load()
    upgrade(url, script_location=script_location)
    run(
        url,
        lambda config: command.revision(config, message=message, autogenerate=True),
        script_location=script_location,
    )


def main(environ: Mapping[str, str] | None = None, argv: list[str] | None = None) -> int:
    """No arguments: apply the migrations. `new NAME`: generate a migration (make migration-new)."""
    source = os.environ if environ is None else environ
    args = [] if argv is None else list(argv)
    new_name = None
    if args:
        if len(args) != 2 or args[0] != "new" or not is_valid_name(args[1]):
            print(USAGE, file=sys.stderr)
            return INVALID_URL_EXIT
        new_name = args[1]
    try:
        database, _command, _Config = _load()
    except ImportError as error:
        print(f"socboard.migrate: {error}", file=sys.stderr)
        return MISSING_PACKAGE_EXIT
    try:
        url = database.resolve_url(source)
        if new_name is None:
            upgrade(url)
        else:
            new_revision(url, new_name)
    except database.DatabaseUrlError as error:
        print(f"socboard.migrate: {error}", file=sys.stderr)
        return INVALID_URL_EXIT
    except MigrationError as error:
        print(f"socboard.migrate: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # fixed text: the class name only, never the message
        print(f"socboard.migrate: the migration failed ({type(error).__name__})", file=sys.stderr)
        return 1
    if new_name is None:
        print("socboard.migrate: the database is at the latest migration")
    else:
        print("socboard.migrate: a new migration file was created; read it before committing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(argv=sys.argv[1:]))
