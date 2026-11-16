---
inclusion: always
---

# Using the deploy server (maintainer's ruling, 2026-11-16)

Agent sessions may run commands on the deploy server through Systems Manager with the `socboard-agent` role (#330), so they can check their own work and record evidence without a hand-off. Commands run as root, on the server that hosts dev and prod. These rules apply to every lane.

1. **Look, don't change.** By default only read: container status, the last log lines, folder owners and modes, the local health URL, and the Postgres checks below. Restarting or stopping a container, editing a file, changing Docker or Caddy, or deleting anything needs the maintainer's ruling for that exact action, recorded in the issue first. A fix goes through an issue, a merge and a deploy, never an edit on the host.
2. **Deploy only through the document.** A deploy is `socboard-deploy` with a published digest, never a hand-run `docker compose`. Prod is deployed only by the promote workflow (#269), never by an agent.
3. **Never read or print a secret.** Do not run `env`, `printenv`, `docker inspect` (it shows the containers' environment), `docker exec ... env`, or read `/run/socboard-deploy/` or `compose.secrets`; do not print `/proc/*/environ`. Logs are read with a tail of at most 20 lines, and anything that looks like a password or a long random string is cut before it is posted.
4. **Postgres: fixed, read-only queries only,** through the local socket inside the container, for example `docker exec socboard-db-1 psql -U postgres -d socboard -At -c "<query>"`. Allowed: role attributes (superuser, create role, create database, owner of `audit_entries`), triggers on `audit_entries` and whether they are enabled, the migration version, row counts per table, server version, database size and connection count. Never select table rows (alert or case text, users, password hashes), never write, never change a setting.
5. **Mind both environments.** Dev and prod share the host. Do nothing that loads the server (no build, no load test, no large download) without the maintainer's ruling. The prod stack is `socboard-prod`; leave it alone unless the issue is about prod.
6. **Record what you checked, once.** Each server check that is evidence goes into the issue's human-task result table or the evidence timeline (`_docs/records/2026-11-16T0000Z-measure-planning-deploy-evidence-timeline.md`) with the date, the command and the result, never a secret, an account number or an ARN (`evidence-and-steps.md`).
7. **If the role is denied an action,** stop and report the action name; do not look for another way to do it.
