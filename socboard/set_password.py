"""Set a new password for one existing user: `python -m socboard.set_password <username>` (#185).

Run by `make set-password ACCOUNT=<username>`. The new password is read from
`SOCBOARD_NEW_PASSWORD`, never from the command line, so it does not appear in a process list or
in shell history. It is hashed as login verifies it (`socboard.passwords.hash_password`) and
written to the `password_hash` of that one user; nothing else changes. `make seed` still never
touches an existing user.

The database is `SOCBOARD_DATABASE_URL`, handled as `make seed` does: a value that is not a valid
database URL exits 2 with fixed text that does not print it, and a database that has not been
migrated exits 1. The password rules are the seed's: at least `MIN_PASSWORD_LENGTH` characters and
no NUL (#144). The username is matched exactly as stored (login strips, then matches exactly), so
a made-up, blank or padded name is refused and the command never creates a user. Every refusal
exits 2 with fixed text, before anything is written. Nothing prints or logs the password, its hash
or the submitted username.

The command issues one UPDATE of one column on `users` and no DDL. It writes no audit entry: the
audit trail belongs to cases. A token issued before the change stays valid until it expires
(#103); the failed-login counters of #178 live in the server process and are not touched.
"""

import os
import sys
from collections.abc import Mapping, Sequence

from socboard.seed import MIN_PASSWORD_LENGTH

PASSWORD_VAR = "SOCBOARD_NEW_PASSWORD"

MISSING_PACKAGE_EXIT = 1
NOT_MIGRATED_EXIT = 1
FAILED_EXIT = 1
REFUSED_EXIT = 2

USAGE = "usage: python -m socboard.set_password <username> (the password is read from " + PASSWORD_VAR + ")"
NOT_MIGRATED_MESSAGE = "the database has not been migrated; run `make migrate` first"
PASSWORD_MESSAGE = f"{PASSWORD_VAR} must be set to at least {MIN_PASSWORD_LENGTH} characters, with no NUL character"
NO_SUCH_USER_MESSAGE = "no such user; nothing was changed"


def password_problem(environ: Mapping[str, str]) -> str | None:
    """The refusal text when the new password is unusable, else None. Never holds the value."""
    value = environ.get(PASSWORD_VAR, "")
    if len(value) < MIN_PASSWORD_LENGTH or "\x00" in value:
        return PASSWORD_MESSAGE
    return None


def username_usable(username: str) -> bool:
    """False for a name no stored user can have: blank, padded, with a NUL or a lone surrogate."""
    if not username or username != username.strip() or "\x00" in username:
        return False
    try:
        username.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def main(environ: Mapping[str, str] | None = None, argv: Sequence[str] | None = None) -> int:
    source = os.environ if environ is None else environ
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print(f"socboard.set_password: {USAGE}", file=sys.stderr)
        return REFUSED_EXIT
    username = arguments[0]
    try:
        from socboard._requirements import require

        require("sqlalchemy")
        require("alembic")
        from sqlalchemy import inspect, select, update

        from socboard import database
        from socboard.models import User
        from socboard.passwords import hash_password
    except ImportError as error:
        print(f"socboard.set_password: {error}", file=sys.stderr)
        return MISSING_PACKAGE_EXIT
    try:
        url = database.resolve_url(source)
        engine = database.create_db_engine(url)
    except database.DatabaseUrlError as error:
        print(f"socboard.set_password: {error}", file=sys.stderr)
        return REFUSED_EXIT
    try:
        with engine.connect() as connection:
            migrated = inspect(connection).has_table("users")
        if not migrated:
            print(f"socboard.set_password: {NOT_MIGRATED_MESSAGE}", file=sys.stderr)
            return NOT_MIGRATED_EXIT
        problem = password_problem(source)
        if problem is not None:
            print(f"socboard.set_password: {problem}", file=sys.stderr)
            return REFUSED_EXIT
        table = User.__table__
        with engine.begin() as connection:
            exists = username_usable(username) and (
                connection.execute(select(table.c.id).where(table.c.username == username)).first()
                is not None
            )
            if not exists:
                print(f"socboard.set_password: {NO_SUCH_USER_MESSAGE}", file=sys.stderr)
                return REFUSED_EXIT
            changed = connection.execute(
                update(table)
                .where(table.c.username == username)
                .values(password_hash=hash_password(source[PASSWORD_VAR]))
            ).rowcount
            if changed != 1:
                # Cannot happen (usernames are unique); rolls back and exits 1 with fixed text.
                raise RuntimeError("expected exactly one row")
    except Exception as error:  # fixed text: the class name only, never the message
        print(f"socboard.set_password: the change failed ({type(error).__name__})", file=sys.stderr)
        return FAILED_EXIT
    finally:
        engine.dispose()
    print("socboard.set_password: the password was changed for 1 user")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
