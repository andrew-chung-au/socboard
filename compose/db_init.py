"""One-shot, idempotent role set-up for the Compose stack (#221): `python /db_init.py`.

Run by the db-init service, in the app's image, as the Postgres superuser on every start. It follows
the role set tests/pg_roles.py proves (decision 8 of the Deploy record): the owner role owns the
database and runs the migrations; the application role does not own the tables, cannot create
anything and cannot disable the audit triggers.

Each role's password is turned into a SCRAM-SHA-256 verifier by libpq on this side, and only the
verifier is sent to the server, so no password is on a command line, in a statement, in the
server's log or in `pg_stat_activity`. Exit 0 when done; exit 1 with fixed text on a failure.
"""

import os
import sys

import psycopg
from psycopg import sql

HOST = "db"
OWNER = "socboard_owner"
APP = "socboard_app"
DATABASE = "socboard"
LOGIN_FAILED = (
    "db-init: could not connect to Postgres as its superuser. The usual cause: the data directory was kept "
    "but the local secrets file was deleted, so the password the data directory holds no longer matches "
    "STACK_PG_SUPERUSER_PASSWORD (README, Compose stack, 'Stopping and starting over')."
)


def connect(environ, dbname):
    return psycopg.connect(
        host=HOST, user="postgres", password=environ["STACK_PG_SUPERUSER_PASSWORD"], dbname=dbname, autocommit=True, connect_timeout=10
    )


def set_up_roles(conn, environ):
    for role, extra, variable in ((OWNER, "", "STACK_PG_OWNER_PASSWORD"), (APP, " NOCREATEDB NOCREATEROLE", "STACK_PG_APP_PASSWORD")):
        verifier = conn.pgconn.encrypt_password(environ[variable].encode(), role.encode(), b"scram-sha-256").decode()
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(sql.SQL(f"{verb} ROLE {{}} LOGIN NOSUPERUSER{extra} PASSWORD {{}}").format(sql.Identifier(role), sql.Literal(verifier)))
    if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)).fetchone():
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(DATABASE), sql.Identifier(OWNER)))
    conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(DATABASE), sql.Identifier(OWNER)))
    conn.execute(sql.SQL("REVOKE CREATE, TEMPORARY ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DATABASE)))


def grant(conn):
    owner, app = sql.Identifier(OWNER), sql.Identifier(APP)
    # Tables and sequences a migration creates later (as the owner role) are usable at once.
    conn.execute(
        sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO {}").format(owner, app)
    )
    conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT USAGE ON SEQUENCES TO {}").format(owner, app))
    # Tables that already exist (a second start).
    conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO {}").format(app))
    conn.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(app))


def main(environ=None) -> int:
    environ = os.environ if environ is None else environ
    try:
        with connect(environ, "postgres") as conn:
            set_up_roles(conn, environ)
        with connect(environ, DATABASE) as conn:
            grant(conn)
    except psycopg.OperationalError:
        # The driver's message names the host and user, so fixed text is printed instead.
        print(LOGIN_FAILED, file=sys.stderr)
        return 1
    print("db-init: roles, database and privileges are in place")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
