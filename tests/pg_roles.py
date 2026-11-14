"""Two throwaway Postgres roles for the audit-trail tests (#193, D6a): an owner and an application role.

Decision 8 of the Deploy record: migrations run as a role that owns the tables, and the application
connects as a role that does not own them and cannot disable triggers or run DDL. Nothing in the
repository creates two roles yet (D11, and the migration-URL follow-up #205), so these tests make
them, by the server's superuser (the test variable's URL), with random names and passwords made for
the run.

- owner: `NOSUPERUSER`, owns a new database, runs the migrations there, so it owns every table.
- app: `NOSUPERUSER NOCREATEDB NOCREATEROLE`, no `CREATE` on the database or `public`, no
  `TRIGGER` privilege, no `SET` privilege on any parameter; `SELECT, INSERT, UPDATE, DELETE,
  TRUNCATE` on the tables and `USAGE` on the sequences. UPDATE, DELETE and TRUNCATE are granted on
  purpose, so the triggers are what refuses them.

The names start with `socboard_t_`, like the test databases, so `conftest.py` can count what is left
on the server after a run. Passwords never appear in a repr, a test id or a failure message: the
fixture returns a `Roles` object whose repr names nothing.
"""

import secrets
from dataclasses import dataclass, field

import db_target
from sqlalchemy import text

ROLE_PREFIX = db_target.NAME_PREFIX


@dataclass(repr=False)
class Roles:
    database: str
    owner: str
    app: str
    owner_url: str = field(repr=False)
    app_url: str = field(repr=False)

    def __repr__(self) -> str:
        return "<Roles (owner and application role; names and passwords not shown)>"


def _admin():
    from socboard import database

    return database.create_db_engine(db_target.server_url()).execution_options(isolation_level="AUTOCOMMIT")


def _url(user: str, password: str, name: str) -> str:
    url = db_target.server_url().set(username=user, password=password, database=name)
    return url.render_as_string(hide_password=False)


def create() -> Roles:
    """Create the two roles and a database the owner owns, migrated as the owner, with the grants."""
    tag = secrets.token_hex(6)
    owner, app, name = f"{ROLE_PREFIX}owner_{tag}", f"{ROLE_PREFIX}app_{tag}", f"{ROLE_PREFIX}{tag}"
    owner_pw, app_pw = secrets.token_hex(16), secrets.token_hex(16)
    roles = Roles(name, owner, app, _url(owner, owner_pw, name), _url(app, app_pw, name))
    try:
        _set_up(roles, owner_pw, app_pw)
    except BaseException:
        drop(roles)  # nothing is left behind by a set-up that failed part-way
        raise
    return roles


def _set_up(roles: Roles, owner_pw: str, app_pw: str) -> None:
    from socboard import database, migrate

    owner, app, name = roles.owner, roles.app, roles.database
    admin = _admin()
    try:
        with admin.connect() as conn:
            q = admin.dialect.identifier_preparer.quote
            # Names and passwords are hex made here, never outside text; quoted anyway.
            conn.execute(text(f"CREATE ROLE {q(owner)} LOGIN NOSUPERUSER PASSWORD '{owner_pw}'"))
            conn.execute(
                text(f"CREATE ROLE {q(app)} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{app_pw}'")
            )
            conn.execute(text(f"CREATE DATABASE {q(name)} OWNER {q(owner)}"))
            conn.execute(text(f"REVOKE CREATE, TEMPORARY ON DATABASE {q(name)} FROM PUBLIC"))
    finally:
        admin.dispose()
    migrate.upgrade(roles.owner_url)
    engine = database.create_db_engine(roles.owner_url)
    try:
        with engine.begin() as conn:
            q = engine.dialect.identifier_preparer.quote
            conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO {q(app)}"))
            conn.execute(text(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {q(app)}"))
    finally:
        engine.dispose()


def drop(roles: Roles) -> None:
    """Drop the database and both roles; safe to call after a failure part-way."""
    admin = _admin()
    try:
        with admin.connect() as conn:
            q = admin.dialect.identifier_preparer.quote
            conn.execute(text(f"DROP DATABASE IF EXISTS {q(roles.database)} WITH (FORCE)"))
            for role in (roles.app, roles.owner):
                conn.execute(text(f"DROP ROLE IF EXISTS {q(role)}"))
    finally:
        admin.dispose()


def listed_roles() -> list[str]:
    """Every role on the server whose name this module's scheme made."""
    admin = _admin()
    try:
        with admin.connect() as conn:
            rows = conn.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname LIKE :p ORDER BY rolname"), {"p": ROLE_PREFIX + "%"}
            )
            return [row[0] for row in rows]
    finally:
        admin.dispose()
