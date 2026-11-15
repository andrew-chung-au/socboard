#!/bin/sh
# One-shot, idempotent role set-up for the Compose stack (#221), run by the db-init service as the
# Postgres superuser on every start. It follows the role set tests/pg_roles.py proves (decision 8
# of the Deploy record): the owner role owns the database and runs the migrations; the application
# role does not own the tables, cannot create anything and cannot disable the audit triggers.
#
# The two passwords come from the environment and are read by psql's own backtick command, so
# they are on no command line. They reach the server as the text of ALTER ROLE; the server logs
# no statement by default, and the stack does not turn that on.
set -eu

psql -X -v ON_ERROR_STOP=1 --dbname postgres <<'SQL'
\set owner_pw `printf %s "$STACK_PG_OWNER_PASSWORD"`
\set app_pw `printf %s "$STACK_PG_APP_PASSWORD"`

SELECT 'CREATE ROLE socboard_owner LOGIN NOSUPERUSER' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'socboard_owner') \gexec
SELECT 'CREATE ROLE socboard_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'socboard_app') \gexec
ALTER ROLE socboard_owner LOGIN NOSUPERUSER PASSWORD :'owner_pw';
ALTER ROLE socboard_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_pw';

SELECT 'CREATE DATABASE socboard OWNER socboard_owner' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'socboard') \gexec
ALTER DATABASE socboard OWNER TO socboard_owner;
REVOKE CREATE, TEMPORARY ON DATABASE socboard FROM PUBLIC;

\connect socboard
-- Tables and sequences a migration creates later (as the owner role) are usable at once.
ALTER DEFAULT PRIVILEGES FOR ROLE socboard_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO socboard_app;
ALTER DEFAULT PRIVILEGES FOR ROLE socboard_owner IN SCHEMA public GRANT USAGE ON SEQUENCES TO socboard_app;
-- Tables that already exist (a second start).
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO socboard_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO socboard_app;
SQL
