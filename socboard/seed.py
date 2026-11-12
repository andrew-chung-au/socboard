"""Seed the database: `python -m socboard.seed` (run by `make seed`).

The database is `SOCBOARD_DATABASE_URL` (default: `socboard.db` in the repository root), handled as
`socboard.migrate` does: a value that is not a valid database URL exits 2 with fixed text that does
not print the value; a database that has not been migrated exits 1 with fixed text telling the user
to run `make migrate`. The asset inventory (synthetic names) is inserted by name: only missing
names are added, so a second run changes nothing and an existing row is never changed or deleted.
User seeding is added by #13.
"""

import os
import sys
from collections.abc import Mapping

MISSING_PACKAGE_EXIT = 1
NOT_MIGRATED_EXIT = 1
INVALID_URL_EXIT = 2

NOT_MIGRATED_MESSAGE = "the database has not been migrated; run `make migrate` first"

# (name, criticality): synthetic assets, all four criticalities.
ASSETS = (
    ("dc-01", "Critical"),
    ("db-prod-01", "Critical"),
    ("payments-gw-01", "Critical"),
    ("web-prod-01", "High"),
    ("vpn-gw-01", "High"),
    ("mail-01", "High"),
    ("fileserver-01", "Medium"),
    ("hr-laptop-07", "Medium"),
    ("dev-box-03", "Low"),
    ("kiosk-02", "Low"),
)


def seed_assets(connection) -> int:
    """Insert the assets whose names are missing; return how many were added."""
    from sqlalchemy import insert, select

    from socboard.models import Asset

    table = Asset.__table__
    existing = set(connection.execute(select(table.c.name)).scalars())
    missing = [{"name": name, "criticality": level} for name, level in ASSETS if name not in existing]
    if missing:
        connection.execute(insert(table), missing)
    return len(missing)


def main(environ: Mapping[str, str] | None = None) -> int:
    source = os.environ if environ is None else environ
    try:
        from socboard._requirements import require

        require("sqlalchemy")
        require("alembic")
        from sqlalchemy import inspect

        from socboard import database
    except ImportError as error:
        print(f"socboard.seed: {error}", file=sys.stderr)
        return MISSING_PACKAGE_EXIT
    try:
        url = database.resolve_url(source)
        engine = database.create_db_engine(url)
    except database.DatabaseUrlError as error:
        print(f"socboard.seed: {error}", file=sys.stderr)
        return INVALID_URL_EXIT
    try:
        with engine.begin() as connection:
            if not inspect(connection).has_table("assets"):
                print(f"socboard.seed: {NOT_MIGRATED_MESSAGE}", file=sys.stderr)
                return NOT_MIGRATED_EXIT
            added = seed_assets(connection)
    except Exception as error:  # fixed text: the class name only, never the message
        print(f"socboard.seed: the seed failed ({type(error).__name__})", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"socboard.seed: {added} asset(s) added, {len(ASSETS) - added} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
