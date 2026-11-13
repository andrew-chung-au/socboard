"""Seed the database: `python -m socboard.seed` (run by `make seed`).

The database is `SOCBOARD_DATABASE_URL` (default: `socboard.db` in the repository root), handled as
`socboard.migrate` does: a value that is not a valid database URL exits 2 with fixed text that does
not print the value; a database that has not been migrated exits 1 with fixed text telling the user
to run `make migrate`. The asset inventory (synthetic names) is inserted by name: only missing
names are added, so a second run changes nothing and an existing row is never changed or deleted.

Users (#13): two Analysts and one Lead, the accounts of the frontend mock (synthetic names). Their
passwords are read at seed time from `SOCBOARD_SEED_PASSWORD_ANALYST_A`, `..._ANALYST_B` and
`..._LEAD`; there is no default. If any of them is missing, empty or shorter than 12 characters,
the seed prints which variable (never its value), exits 2 and writes nothing at all. Users are
inserted by username: an existing user is never changed, its password included (password reset
is out of scope). Passwords are stored only as salted scrypt hashes (`socboard.passwords`).
"""

import os
import sys
from collections.abc import Mapping

MISSING_PACKAGE_EXIT = 1
NOT_MIGRATED_EXIT = 1
INVALID_URL_EXIT = 2
BAD_PASSWORD_EXIT = 2

MIN_PASSWORD_LENGTH = 12

# (username, display name, role, password variable): the frontend mock's accounts.
USERS = (
    ("analyst-a", "Ana Alvarez", "analyst", "SOCBOARD_SEED_PASSWORD_ANALYST_A"),
    ("analyst-b", "Ben Okafor", "analyst", "SOCBOARD_SEED_PASSWORD_ANALYST_B"),
    ("lead", "Lee Tanaka", "lead", "SOCBOARD_SEED_PASSWORD_LEAD"),
)

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


def read_seed_passwords(environ: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """(username -> password, problems). Each problem names a variable, never its value."""
    found: dict[str, str] = {}
    problems: list[str] = []
    for username, _, _, variable in USERS:
        value = environ.get(variable, "")
        if len(value) < MIN_PASSWORD_LENGTH:
            problems.append(f"{variable} must be set to at least {MIN_PASSWORD_LENGTH} characters")
        else:
            found[username] = value
    return found, problems


def seed_users(connection, seed_passwords: Mapping[str, str]) -> int:
    """Insert the users whose usernames are missing; return how many were added.

    An existing user is left exactly as it is. A password is hashed only for a user being added.
    """
    from sqlalchemy import insert, select

    from socboard.models import User
    from socboard.passwords import hash_password

    table = User.__table__
    existing = set(connection.execute(select(table.c.username)).scalars())
    missing = [
        {
            "username": username,
            "display_name": display_name,
            "role": role,
            "password_hash": hash_password(seed_passwords[username]),
        }
        for username, display_name, role, _ in USERS
        if username not in existing
    ]
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
        with engine.connect() as connection:
            inspector = inspect(connection)
            migrated = inspector.has_table("assets") and inspector.has_table("users")
        if not migrated:
            print(f"socboard.seed: {NOT_MIGRATED_MESSAGE}", file=sys.stderr)
            return NOT_MIGRATED_EXIT
        seed_passwords, problems = read_seed_passwords(source)
        if problems:
            # Checked before any write, so nothing at all is seeded.
            for problem in problems:
                print(f"socboard.seed: {problem}", file=sys.stderr)
            return BAD_PASSWORD_EXIT
        with engine.begin() as connection:
            added = seed_assets(connection)
            users_added = seed_users(connection, seed_passwords)
    except Exception as error:  # fixed text: the class name only, never the message
        print(f"socboard.seed: the seed failed ({type(error).__name__})", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"socboard.seed: {added} asset(s) added, {len(ASSETS) - added} already present")
    print(f"socboard.seed: {users_added} user(s) added, {len(USERS) - users_added} already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
