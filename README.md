# SOCBoard

SOCBoard is a web-based case management board for a security operations centre (SOC). Alerts arrive through an API, receive a severity from fixed rules, and appear in a shared analyst queue. Analysts claim an alert, investigate it and move it through a defined workflow to closure. Every status change, assignment and note is recorded in an audit trail.

All data is synthetic. The graded version uses no machine learning in its critical path, so every severity and every status change can be explained and tested.

[![verify](https://github.com/andrew-chung-au/socboard/actions/workflows/verify.yml/badge.svg)](https://github.com/andrew-chung-au/socboard/actions/workflows/verify.yml) [![compose](https://github.com/andrew-chung-au/socboard/actions/workflows/compose.yml/badge.svg)](https://github.com/andrew-chung-au/socboard/actions/workflows/compose.yml) [![postgres](https://github.com/andrew-chung-au/socboard/actions/workflows/postgres.yml/badge.svg)](https://github.com/andrew-chung-au/socboard/actions/workflows/postgres.yml)

## Reviewer guide

This section is for the peer reviewer scoring the course rubric (`_docs/planning/rubric.md`: 12 criteria, 24 points, 10 to pass). Each row says what exists, where to open the evidence, and the score we believe it supports. The score is a suggestion; please judge each row yourself. Rows marked **in progress** say exactly what is done and what is left, and link the open work.

### At a glance

- **What it is:** a case board for a security operations team. Alerts arrive through an API, get a severity from a fixed rule table, wait in a shared queue, and are claimed, investigated and closed by analysts. Every step is written to an append-only audit trail.
- **Stack:** React and TypeScript (frontend), FastAPI and SQLAlchemy on Python 3.12 (backend), SQLite for local work and Postgres 16 deployed, Docker Compose, Terraform on AWS, GitHub Actions.
- **Built by an agent team** working from a written spec and a public tracker: 95 issues closed and 178 pull requests merged, each issue groomed with acceptance criteria and checked by an independent QA role; 29 decision records explain the choices.

### Try it in five minutes (needs Docker)

```sh
make compose-up      # Postgres, the app and a local proxy; makes local secrets on the first run, prints none
grep '^SOCBOARD_SEED_PASSWORD_LEAD=' compose.secrets   # your local lead password (a random value made on your machine)
```

Open http://127.0.0.1:8080/ and sign in as `lead` with that password. `make compose-down` stops it and keeps the data. Without Docker, `make run` starts the same app on SQLite (see [Development](#development)).

### Where to find each criterion

| # | Criterion (max) | Suggested | What exists, and where to open it |
|---|---|---|---|
| 1 | Problem description (2) | 2 | The problem, what the system does and how it must behave: [Problem](#problem), [Solution](#solution), [Core functionality](#core-functionality), [Case workflow](#case-workflow), [Expected behavior](#expected-behavior), [Severity rules](#severity-rules). |
| 2 | Product specification (2) | 2 | The spec with users, journeys, stories, invariants and phases: [`_docs/specs.md`](_docs/specs.md). The public tracker: [issues](https://github.com/andrew-chung-au/socboard/issues?q=is%3Aissue). Every issue carries acceptance criteria and the agents' own `PM:`, `Engineer:`, `QA:` and `Orchestrator:` comments. Good examples: [#178](https://github.com/andrew-chung-au/socboard/issues/178) (login limiting), [#221](https://github.com/andrew-chung-au/socboard/issues/221) (the Compose stack), [#237](https://github.com/andrew-chung-au/socboard/issues/237) (the deploy script, with two independent QA reviews). |
| 3 | Agent team specification (2) | 2 | Three roles and how work passes between them: [`_docs/agent-kit/process.md`](_docs/agent-kit/process.md) (lifecycle, tiers, hand-offs) and the role files [PM](_docs/agent-kit/team/pm.md), [Software engineer](_docs/agent-kit/team/software-engineer.md), [QA engineer](_docs/agent-kit/team/qa-engineer.md). How parallel sessions share the work without collisions: [`_docs/planning/deploy-coordination.md`](_docs/planning/deploy-coordination.md). See [The agent team](#the-agent-team) below. |
| 4 | Context management (2) | 2 | [`AGENTS.md`](AGENTS.md) describes the project, its commands and rules, and its "Read when" list points to the modular docs: process, roles, procedures, the spec and [`_docs/index.md`](_docs/index.md), the index of every project document. Always-on rules for the sessions are in [`.kiro/steering/`](.kiro/steering). |
| 5 | Application (2) | 2 | Frontend ([`frontend/`](frontend)), backend ([`socboard/`](socboard)) and a database (SQLite locally, Postgres in the stack), connected end to end. One command runs it locally: `make run` (SQLite) or `make compose-up` (Postgres). [Development](#development), [Compose stack](#compose-stack). |
| 6 | Testing (2) | 2 | Unit tests and in-process integration tests (the full user journeys in [`tests/test_journeys.py`](tests/test_journeys.py)) run with one command, `make test`, which also runs the frontend's tests; CI runs them on every push and pull request ([verify](https://github.com/andrew-chung-au/socboard/actions/workflows/verify.yml)). The same suite runs on Postgres ([postgres](https://github.com/andrew-chung-au/socboard/actions/workflows/postgres.yml)). Against the running stack: `make test-stack` (journeys over HTTP through the proxy) and `make e2e` (a real browser with two analysts and a lead), both in CI ([compose](https://github.com/andrew-chung-au/socboard/actions/workflows/compose.yml)). [Tests against the stack](#tests-against-the-stack), [Browser test](#browser-test). |
| 7 | Containerization (2) | 2 | One image serves the API and the built frontend ([`Dockerfile`](Dockerfile), built and checked in CI). The full system, including Postgres, starts with one command: `make compose-up` ([`compose.yaml`](compose.yaml)). [Container image](#container-image), [Compose stack](#compose-stack). |
| 8 | Deployment (2) | **in progress** (1 now, 2 when the app is live) | The AWS infrastructure is built by Terraform ([`terraform/`](terraform)) and applied: the server, its encrypted data volume, daily snapshots, HTTPS through host Caddy, and keyless CI access. The public hostnames already answer over HTTPS with valid certificates: dev https://44-216-250-242.sslip.io/ and prod https://prod-44-216-250-242.sslip.io/. Every check made on the real systems is dated in the [evidence timeline](_docs/records/2026-11-16T0000Z-measure-planning-deploy-evidence-timeline.md). The first deploy of the app ([#237](https://github.com/andrew-chung-au/socboard/issues/237)) stopped safely at its database step on a file-permission bug that only a real host could show; the fix is in progress, and this row links the live app once it runs. |
| 9 | CI/CD (3) | **in progress** (1 now, 3 when prod is promoted) | CI runs every test on every pull request (1). Built and waiting on the first deploy: CD to dev on every green push to `main` ([#238](https://github.com/andrew-chung-au/socboard/issues/238), [Deploy to dev](#deploy-to-dev)) (2). In progress: a manual, approved promotion of the same image to production, with rollback ([#269](https://github.com/andrew-chung-au/socboard/issues/269)), behind the `deploy-prod` Environment's required reviewer (3). The tested image is published once and deployed by digest ([Published image](#published-image)). |
| 10 | Observability (2) | **planned** (0 now) | The plan and its issues: [`_docs/planning/roadmap-4-devops.md`](_docs/planning/roadmap-4-devops.md), with the signal contract already written ([#285](https://github.com/andrew-chung-au/socboard/issues/285)): one key journey, ingest to queue, with a metric, a trace and structured logs sharing a trace id, and no customer data or credentials in telemetry. |
| 11 | Alerting on user impact (1) | **planned** (0 now) | An alert when new alerts take longer than the spec's 5 seconds to reach the queue ([#296](https://github.com/andrew-chung-au/socboard/issues/296)). |
| 12 | On-call agent (2) | **planned** (0 now) | An agent woken by that alert opens the smallest fix as a pull request, proved by a drill with a planted defect ([#297](https://github.com/andrew-chung-au/socboard/issues/297) to [#299](https://github.com/andrew-chung-au/socboard/issues/299)). |
| | **Total** | **16 now, 24 planned** | Already above the pass mark of 10. This table is updated as each in-progress row lands. |

### What this project shows

- **Security built in, not added later.** The audit trail is append-only at the database (triggers on SQLite and on Postgres, where the app connects as a role that owns nothing and cannot disable them) and again in the application. Deployed mode refuses to start with a missing or weak secret. Login and ingest are rate-limited without letting anyone lock out a user. Alert text is treated as untrusted and escaped. Records: [audit trail on Postgres](_docs/decisions/2026-11-14T1946Z-postgres-audit-triggers.md), [the Deploy decisions](_docs/decisions/2026-11-13T2156Z-deploy.md).
- **Least-privilege cloud access.** CI reaches AWS only through short-lived GitHub OIDC tokens, with separate plan, apply and deploy roles. No role can change IAM, so CI cannot widen its own access, and branches other than `main` were shown to be refused ([#156](https://github.com/andrew-chung-au/socboard/issues/156), [the record](_docs/decisions/2026-11-14T0928Z-ci-oidc-roles.md)).
- **One tested image, deployed by digest.** The image the integration and browser tests ran against is the one published and deployed; nothing is rebuilt between test and deploy, and there is no `latest` tag.
- **Verified on real systems, and honest about it.** Every assumption about AWS was marked `VERIFY:` until a real apply settled it, and each result is in the [evidence timeline](_docs/records/2026-11-16T0000Z-measure-planning-deploy-evidence-timeline.md), including the problems found and fixed on the way (for example a role identifier that appeared in a public CI log was masked and the log deleted).
- **A disciplined agent process.** Each issue is groomed by a PM role, built by an Engineer role and checked by a QA role that changes nothing; security-sensitive issues get a second, independent QA review. Every session leaves a summary in [`_session-summaries/`](_session-summaries).

### The agent team

| Role | Does | Never does |
|---|---|---|
| PM | Turns the spec into issues with checkable acceptance criteria, proposes a tier, records the issue's text hash so the approved version is fixed | Writes code, or starts work on an issue the maintainer has not approved |
| Software engineer | Implements one approved issue on its own branch, with tests, and opens a pull request | Changes protected files without the maintainer's approval, or weakens a test |
| QA engineer | Checks the result against every acceptance criterion and posts a pass or fail with evidence | Changes anything |
| Orchestrator | Runs the roles in order, claims and closes issues, and brings the maintainer each decision | Plans, grooms, implements or tests itself |
| Maintainer (human) | Approves issues, merges, applies Terraform and runs the steps only a person can | |

Small changes (Light), normal changes (Standard) and security-sensitive changes (Heavy, which add a decision record and a second QA) follow the tiers in [`_docs/agent-kit/process.md`](_docs/agent-kit/process.md).

## Problem

Small security teams receive alerts from many sources: failed logins, malware detections, unusual network traffic. Without a shared system, alerts sit in inboxes, chat threads and spreadsheets. This causes three problems:

- Nobody knows who owns an alert. Two analysts investigate the same one, or nobody does.
- There is no record of what happened. When an alert is closed, there is no trace of who decided what, or why.
- Priority is inconsistent. Each analyst judges severity differently, so urgent alerts wait behind noisy ones.

## Solution

SOCBoard is a web-based alert triage case manager. Alerts arrive through an API, get a consistent severity from fixed rules, and appear in an analyst queue. Analysts claim an alert, investigate it and move it through a defined workflow. Every step is recorded.

The data is synthetic. The graded version has no machine learning in its critical path, so every severity and every status change can be explained and tested.

## Users

- Analyst: works the queue, adds notes, moves cases through the workflow.
- Lead: reviews the queue, reassigns cases and checks the audit trail.

## Core functionality

1. Ingest: alerts come in through the API with a source, type, affected asset and timestamp.
2. Score: a fixed rule set assigns severity (for example, by alert type and asset criticality).
3. Queue: analysts see open alerts sorted by severity and age.
4. Triage: an analyst claims an alert, adds notes and moves it through the workflow.
5. Audit: every status change, assignment and note records who, what and when.

## Case workflow

New → Investigating → Closed (with a required closing reason, for example true positive, false positive or duplicate).

Blocked transitions, such as closing a New alert without investigating it, return a clear error and are never silently allowed.

## Expected behavior

- A new alert appears in the queue within 5 seconds with a severity.
- An alert can have only one owner at a time.
- Closing a case requires a reason.
- The audit trail for a case can be read in order, start to finish.
- Alert text is untrusted input: it is escaped when shown and never treated as instructions.

## Severity rules

Severity comes from a fixed table of alert type and asset criticality (`socboard/scoring.py`). There is no machine learning: the same inputs always give the same severity and the same rule text.

| Type | Low | Medium | High | Critical |
|---|---|---|---|---|
| failed_login | Low | Low | Medium | High |
| malware_detection | Medium | High | Critical | Critical |
| unusual_network_traffic | Low | Medium | High | High |

- Defaults: an alert type that is not in the table gets Medium (rule `unknown type, default -> Medium`). An asset that is not in the inventory is scored as criticality Medium (rule `malware_detection x Medium (asset unknown, criticality Medium) -> High`, for example).
- Matching is exact and case-sensitive, with no trimming, for both the alert type and the asset name: `Malware_Detection` and `malware_detection ` are unknown types.
- Asset criticality comes from the seeded inventory (`make seed`, after `make migrate`).

## Development

Requires Python 3.12, uv, bun and make. From the project folder:

```sh
make install   # install the Python and frontend packages (needs bun and the npm registry); run once on a fresh checkout, before test or verify
make test      # run the pytest suite, then the frontend tests
make verify    # run tests, whitespace and weakened-test checks
```

Quick start, the whole local stack with one command:

1. `make install` (once, on a fresh checkout).
2. Set five environment variables in your shell: `SOCBOARD_TOKEN_SECRET`, `SOCBOARD_SEED_PASSWORD_ANALYST_A`, `SOCBOARD_SEED_PASSWORD_ANALYST_B`, `SOCBOARD_SEED_PASSWORD_LEAD` and `SOCBOARD_INGEST_API_KEY`. What each must hold is in the list below and in the ingest paragraph. The seed passwords are the sign-in passwords of `analyst-a`, `analyst-b` and `lead`; a user that already exists keeps its old password.
3. `make run`: checks the five variables first and starts nothing if one is missing or too short, then migrates and seeds the database, starts the backend on http://127.0.0.1:8000/ and the frontend, on the real API (mock off), on http://127.0.0.1:5173/. Leave `SOCBOARD_PORT` unset here: the frontend sends `/api` to port 8000.
4. `make stop` stops both.

Sign-in needs four environment variables, set once (names only here; never commit values):

- `SOCBOARD_TOKEN_SECRET`: the token signing secret, at least 32 characters, not counting leading and trailing spaces. The backend refuses to start without it. A good secret is a random value of at least 32 characters, for example from a password manager or `openssl rand -base64 32`.
- `SOCBOARD_SEED_PASSWORD_ANALYST_A`, `SOCBOARD_SEED_PASSWORD_ANALYST_B` and `SOCBOARD_SEED_PASSWORD_LEAD`: the passwords `make seed` gives the users `analyst-a`, `analyst-b` and `lead`, at least 12 characters each. All three are needed on every run, even when every user already exists. Seeding fails, and adds nothing, if one is missing or short.

Frontend against the real backend: the frontend uses an in-memory mock unless `VITE_USE_MOCK` is `false`. To use the backend: run `make migrate`, `make seed` and `make backend-run` (it listens on 127.0.0.1:8000), then export the variable in the shell and start the dev server (the Makefile does not set it; the value passes through from the shell):

```
export VITE_USE_MOCK=false
make frontend-start
```

The dev server sends every `/api` request to the backend on 127.0.0.1:8000 (`frontend/vite.config.ts`), so the backend needs no cross-origin (CORS) setting. The proxy is for the dev server only; a built site gets `/api` from the backend itself (see "Serving the built site" below), or needs `/api` routed to the backend by whatever else serves it.

Where the sign-in token is kept: in memory while the page is open, and in the browser's session storage (key `socboard.session`) so a refresh keeps the session. It is never put in local storage and never logged. It is cleared on log out and on any 401 from the server, which returns the user to the sign-in screen. Closing the tab ends the session.

Ingest (`POST /api/alerts`) takes the key in the `X-API-Key` header, from `SOCBOARD_INGEST_API_KEY` (at least 16 characters; without it ingest answers 401 to every request and the app logs one warning). Alerts are not de-duplicated, by design for Core: posting the same alert twice creates two cases.

Serving the built site: after `make frontend-build`, the backend serves `frontend/dist` itself: `/` and any screen route without a file extension (such as `/cases/1`) return `index.html`, a file in the folder is returned as it is, a missing path with an extension is a 404, and everything under `/api` is unchanged. Only GET and HEAD get the site. The folder comes from `SOCBOARD_STATIC_DIR` (default: `frontend/dist` in the repository root, whatever the working directory; a relative value is relative to the working directory) and is read when the backend starts, so restart it after a rebuild only if `dist` did not exist before. A missing folder, or one without `index.html`, is not an error: the backend then serves the API only. `index.html` is sent with `Cache-Control: no-cache` and files under `assets/` with a one-year `immutable` lifetime (their names carry a content hash).

Binding beyond localhost: the backend listens on `127.0.0.1`, `localhost` or `::1` only, and `make backend-run` always does. The one exception is the flag `--allow-non-loopback-host` of `python -m socboard.server`, which lets `SOCBOARD_HOST` be an IP address such as `0.0.0.0`. Only the container image's start command may pass it, because the app must listen on an address Docker and the reverse proxy can reach; the image's port is published on the host's loopback only. It is a flag, not an environment variable, so a variable left in a developer's shell cannot open the server to the network. Do not pass it by hand or from `make`. The flag takes no value, and a wrong or unknown argument exits 2.

Postgres: set `SOCBOARD_DATABASE_URL` to a `postgresql+psycopg://` URL and `make migrate`, `make seed` and the backend use Postgres 16 instead of the SQLite default. CI runs migrate, seed, a health check and a login against a Postgres 16 service, and a second job runs the Python tests there (`.github/workflows/postgres.yml`; next paragraph). A failed Postgres connection is logged as fixed text, without the user or database name. On Postgres the audit trail is append-only too: migration `0002` adds four triggers that refuse an UPDATE, a DELETE, an insert that reuses an id and a TRUNCATE of `audit_entries`, and they stay on when `session_replication_role` is `replica`. They hold against a role that does not own the table; the table's owner and any superuser can still disable or drop them. The Compose stack ("Compose stack" below) creates two roles, an owner that runs the migrations and an application role that cannot switch the triggers off; `make migrate` on a host still connects with the one `SOCBOARD_DATABASE_URL`, so the role it uses owns the table and can switch them off, until the migration-URL follow-up #205.

Tests on Postgres: `make test` and `make verify` run the Python tests on SQLite. To run them on a Postgres 16 server instead, set the test variable named at the top of `tests/db_target.py` (it is not a runtime setting, so it is not in the table below) to a `postgresql+psycopg://` URL of a server whose databases the tests may create and drop, and run `uv run --locked pytest`. Each test gets a database of its own, named `socboard_t_` plus 12 hex digits, dropped when the test ends; the template databases those are copied from are dropped when the run ends. A run that is killed can leave them behind: they all start with `socboard_t_`. A value that is not such a URL stops the run without printing it. Tests marked `sqlite_only` need an SQLite file or SQLite features and are deselected on Postgres; tests marked `postgres_only` (the Postgres audit triggers and roles) are deselected on SQLite; the run prints both counts.

### Runtime variables

Every runtime variable, from the one list in `socboard/settings.py` (a test fails if this table and that list disagree on a name or on the Secret and Required in deployed mode columns). The example env file at the repository root has the same names, with no values. Never put a value in this file, a commit or an issue.

| Variable | Secret | Required in deployed mode | Rule |
|---|---|---|---|
| `SOCBOARD_TOKEN_SECRET` | yes | yes | At least 32 characters, not counting leading and trailing spaces; required in every mode. In deployed mode it must also not be one repeated character, whitespace ignored (so `s s s ...` is refused). |
| `SOCBOARD_SEED_PASSWORD_ANALYST_A` | yes | no (the seed step needs it) | At least 12 characters; read by `make seed` only. |
| `SOCBOARD_SEED_PASSWORD_ANALYST_B` | yes | no (the seed step needs it) | At least 12 characters; read by `make seed` only. |
| `SOCBOARD_SEED_PASSWORD_LEAD` | yes | no (the seed step needs it) | At least 12 characters; read by `make seed`, and by the smoke check. |
| `SOCBOARD_INGEST_API_KEY` | yes | yes | At least 16 characters; without it ingest is closed and one warning is logged. In deployed mode it is required: at least 16 characters, no leading or trailing whitespace (HTTP strips it from the header, so such a key could never match), and not one repeated character, whitespace ignored. Also read by the smoke check. |
| `SOCBOARD_DATABASE_URL` | yes | yes | A valid database URL; default `socboard.db` in the repository root. In deployed mode it must be set. |
| `SOCBOARD_LOGIN_MAX_FAILURES` | no | no | Failed logins allowed per client address and username within the window before login answers 429; a whole number of 1 or more (default 5). See "Login attempt limits". |
| `SOCBOARD_LOGIN_CLIENT_MAX_FAILURES` | no | no | Failed logins allowed per client address, across all usernames, within the window; a whole number of 1 or more (default 20). |
| `SOCBOARD_LOGIN_WINDOW_SECONDS` | no | no | The length of the window, in seconds; a whole number of 1 or more (default 300). |
| `SOCBOARD_LOGIN_MAX_TRACKED` | no | no | The most (client, username) and client counters kept at once; the least recently failed is dropped first; a whole number of 1 or more (default 10000). |
| `SOCBOARD_TRUSTED_PROXIES` | no | no | A comma-separated list of IP addresses of reverse proxies whose `X-Forwarded-For` header is believed (default none: the header is ignored). See "Login attempt limits". |
| `SOCBOARD_INGEST_MAX_REQUESTS` | no | no | Requests with a valid ingest key allowed within the window before ingest answers 429; a whole number of 1 or more (default 60). See "Ingest rate limit". |
| `SOCBOARD_INGEST_WINDOW_SECONDS` | no | no | The length of the ingest window, in seconds; a whole number of 1 or more (default 60). |
| `SOCBOARD_NEW_PASSWORD` | yes | no | At least 12 characters, no NUL character; read by `make set-password` only. See "Resetting a user's password". |
| `SOCBOARD_HOST` | no | no | `127.0.0.1`, `localhost` or `::1` (default `127.0.0.1`); see "Binding beyond localhost". |
| `SOCBOARD_PORT` | no | no | A whole number from 1 to 65535 (default 8000). |
| `SOCBOARD_STATIC_DIR` | no | no | The built frontend folder (default `frontend/dist`); see "Serving the built site". |
| `SOCBOARD_MODE` | no | no | Exactly `deployed` turns deployed mode on. Any other value, including an empty one or a different case, leaves it off. `make run`, `make backend-run` and the tests never set it. A development run never starts in deployed mode: `make run` refuses it (exit 2, nothing started) and `make backend-run` removes it from the backend's environment and prints a line saying so. |

Deployed mode: with `SOCBOARD_MODE` set to `deployed`, the backend refuses to start (exit code 2) when the token secret, the ingest key or the database URL is missing, empty, too short or made only of whitespace, when the secret or the key is one repeated character (whitespace ignored), or when the key has leading or trailing whitespace. The message names the variable and never prints its value. Outside deployed mode nothing else changes.

Login attempt limits: `POST /api/login` counts failed logins per client address and username, and per client address across usernames. At the limit it answers 429 `rate_limited` with a `Retry-After` header until enough old failures leave the window; a real and a made-up username get the same answer. The check runs before any password work. A successful login counts nothing. Because the three seeded usernames are public, the counters are keyed on the client address too: an outsider can only limit their own address, never a reviewer signing in from another one. The counters live in the process, assume one worker, and reset on restart; a second worker would multiply the limits. The client address is the socket peer. `X-Forwarded-For` is read only when the peer is listed in `SOCBOARD_TRUSTED_PROXIES`, and then the address is the rightmost entry that is not itself a listed proxy, so a forged header cannot choose another client's bucket. Behind a reverse proxy, list the proxy's address: left empty, every client shares the proxy's address, and one outsider's failures would refuse every login for a window. The server (uvicorn) has its own list, `FORWARDED_ALLOW_IPS` (default `127.0.0.1`): when the connection comes from an address in it, uvicorn replaces the peer address with the one from the forwarded header before the app sees it. Two cases follow. A proxy on the same host connects from `127.0.0.1`, uvicorn already supplies the client address, the app then sees an untrusted peer and ignores the header, and `SOCBOARD_TRUSTED_PROXIES` stays empty. A proxy on another address (a container, say) is not in uvicorn's default list, so the app reads the header itself and the proxy's address goes in `SOCBOARD_TRUSTED_PROXIES`. The image's start command must leave `FORWARDED_ALLOW_IPS` at its default or list only the proxy's address, never `*`: with `*` uvicorn believes any caller's header, and a forged one would choose its bucket. An IPv4-mapped IPv6 peer (`::ffff:a.b.c.d`) is treated as the IPv4 address.

Ingest rate limit: `POST /api/alerts` accepts `SOCBOARD_INGEST_MAX_REQUESTS` requests with a valid ingest key per `SOCBOARD_INGEST_WINDOW_SECONDS` (default 60 per 60 seconds). Above that it answers 429 `rate_limited` with a `Retry-After` header and stores nothing (no alert, case or audit entry). The limit belongs to the ingest key as a whole, not to the `source` field, which a caller can change on every request. A missing or wrong key is 401 first and does not use the budget; a request with the valid key counts even if its body is then refused with 422; a refused request is not counted and does not extend the wait. The count lives in the process, assumes one worker, and resets on restart; a second worker would multiply the rate.

Resetting a user's password: `make seed` never changes an existing user, so a new password for one is set with `make set-password ACCOUNT=<username>`. The new password is read from `SOCBOARD_NEW_PASSWORD`, which you set in your own shell or secret store, never on the command line; it is hashed the way login checks it and written to that one user's row, and nothing else changes. The rules are the seed's (at least 12 characters, no NUL character). The username must match a stored one exactly (login trims and then matches exactly, so no case folding or trimming here); a missing user, a blank or padded name, or a bad password exits 2 with fixed text and changes nothing, and the command never creates a user. It never prints the password, its hash or the name. It writes no audit entry, because the audit trail belongs to cases. Limits: a token issued before the change stays valid until it expires (8 hours); restarting the backend with a new `SOCBOARD_TOKEN_SECRET` signs everyone out; the failed-login counters of "Login attempt limits" are not cleared.

Smoke check: `python -m socboard.smoke <url>` (or `make smoke URL=<url>`) checks a running app in five steps, each named in the output: (1) `GET /api/health` answers 200; (2) a login as `lead` succeeds; (3) one synthetic alert (source `smoke-check`, type `failed_login`, asset `smoke-check-host`, the current UTC time) is ingested with the key and answers 201; (4) that case is in `GET /api/queue` within 5 seconds of the ingest response, polled every half second and found by the `id` in the 201 response; (5) the Lead can read the case's audit trail and it holds the `ingested` entry. It exits 0 when every step passed, 1 when one failed (the output names the step, the HTTP status and, for a refusal, the contract `code` when it is a short lower-case word), and 2 when the URL or a credential is refused, before any request (`make smoke` reports every failure as 2, because make does; the output says which step failed). It never prints a password, a key, a token or a response body, deletes nothing, and **each full run adds one New case to the queue**. The password comes from `SOCBOARD_SEED_PASSWORD_LEAD` and the key from `SOCBOARD_INGEST_API_KEY`, never from an argument. A password reset with `make set-password` must be copied into `SOCBOARD_SEED_PASSWORD_LEAD` before the next smoke check, otherwise step 2 fails with 401. The URL must be `http` only for `localhost`, `127.0.0.1` or `::1`, otherwise `https`; it may not hold a user name, a password, a query or a fragment, and it must be ASCII only (give an internationalized host name in its `xn--` form). An answer that ends before the length the server declared, or a chunked answer that ends before its last chunk, fails its step with the text "the answer was cut short". No redirect is followed, so the key and the token go only to the origin you gave. One request stops after 10 seconds and the whole run after 60, each counted from when the connection starts; time spent resolving a name is not counted. A 429 from login or ingest is reported as that step failing with the code `rate_limited` and is not retried (login counts failed logins only, so it comes after earlier failures from the same address; a wrong password is one 401 and adds one failure to the counters). Run on the server, against the app's own port: `docker run --network host` of the same image with the two variables passed in from the host and the command `python -m socboard.smoke http://127.0.0.1:<port>` (`VERIFY:` on the first real run, with the image of #190, which client address the app sees, because it feeds the login counters). Run from CI against the public URL, health route only: `make smoke URL=<public https URL> HEALTH_ONLY=1`, which needs no credential.

Build arguments, not runtime variables: `VITE_API_BASE_URL`, `VITE_FEATURE_REASSIGN`, `VITE_POLL_INTERVAL_MS` and `VITE_USE_MOCK` are fixed when the frontend is built (`make frontend-build`), so changing them later needs a rebuild.

### Container image

The `Dockerfile` builds one image that serves the API and the built frontend from one origin. It is built by CI on every push and pull request (`.github/workflows/image.yml`), which also checks what is in it; the agent's sandbox has no Docker.

- **Build:** `docker build --tag socboard-image .` from the repository root. Two optional build arguments, `VITE_FEATURE_REASSIGN` and `VITE_POLL_INTERVAL_MS`, are passed with `--build-arg`. `VITE_API_BASE_URL` stays unset, so the client calls `/api` on the same origin. `VITE_USE_MOCK` is not a build argument: the `Dockerfile` sets it to `false` with `ENV`, so an image cannot be built with the mock on.
- **Start:** `python -m socboard.server --allow-non-loopback-host`, in exec form, with `SOCBOARD_HOST` set to `0.0.0.0` in the image (the flag, not the variable, is what lets the server bind it). The app listens on port 8000 as user 10001, not root. The image also holds `python -m socboard.migrate` and `python -m socboard.seed`, the one-shot commands to run in the container before the first start.
- **One worker:** the start command has no `--workers` and the image sets no `WEB_CONCURRENCY`, because the login and ingest limits live in the process and assume one worker.
- **Forwarded addresses:** the image leaves `FORWARDED_ALLOW_IPS` at uvicorn's default (`127.0.0.1`) and does not set `SOCBOARD_TRUSTED_PROXIES`, because it cannot know the proxy's address. Whoever runs the container behind a reverse proxy at another address lists that proxy's address in `SOCBOARD_TRUSTED_PROXIES` (see "Login attempt limits"). If `FORWARDED_ALLOW_IPS` is set at run time, it lists only the proxy's address, never `*`: with `*` uvicorn believes any caller's header, and a forged one would choose its bucket.
- **Settings:** none is baked in. Deployed mode (`SOCBOARD_MODE` set to `deployed`) needs the token secret, the ingest key and the database URL at run time, and the seed needs the three seed passwords; the image holds no secret, no seeded password and no demo data. A SQLite database URL must point into a folder the container's user can write.

### Compose stack

`make compose-up` starts the whole system with Docker Compose (`compose.yaml`); `make compose-down` stops it. `make run` is the development command (SQLite, the Vite dev server); the stack is the deployed shape: Postgres 16, the container image and a local proxy. The agent's sandbox has no Docker, so CI (`.github/workflows/compose.yml`) is the check.

- **Order:** `db` (healthy), then `db-init`, `migrate` and `seed` (one-shot steps that must exit 0), then `app` (healthy), then `proxy`. A failing step stops the ones after it.
- **Roles:** `db-init` (`compose/db_init.py`, run in the app's image) creates two roles on every start and sets their passwords, sending the server only a SCRAM verifier, never a password. `migrate` connects as the owner role, which owns the tables. `seed` and `app` connect as the application role: not a superuser, not the owner, no right to create anything, and it can read and write the tables but the triggers refuse an update, delete or truncate of the audit trail and it cannot disable them (decision 8 of the Deploy record). The owner and the Postgres superuser still can. Only `db` and `db-init` hold the superuser's password.
- **Ports:** `app` is published on `127.0.0.1:8000` and `proxy` on `127.0.0.1:8080`, loopback only (`STACK_APP_PORT` and `STACK_PROXY_PORT` change them). `db` publishes none. The proxy is Caddy over plain HTTP at a fixed address on the stack's own network (whose gateway is pinned to `10.231.77.1`: with a pool and no gateway Docker takes the pool's first address, `10.231.77.128` on Docker Engine 29.1.3, and the server's trusted proxy address assumes `.1`; changing it recreates an existing stack's network, so run `make compose-down` first, which keeps the data), and `app` trusts exactly that address for `X-Forwarded-For` (`SOCBOARD_TRUSTED_PROXIES`, unless `STACK_TRUSTED_PROXIES` sets another); `FORWARDED_ALLOW_IPS` is left at its default. The app runs in deployed mode with one worker.
- **Secrets:** the first `make compose-up` writes `compose.secrets` (git-ignored, readable by you only) with a random value for the five variables of `make run` and three database passwords: `STACK_PG_SUPERUSER_PASSWORD`, `STACK_PG_OWNER_PASSWORD` and `STACK_PG_APP_PASSWORD`. It never prints a value, never rewrites a line you set, and refuses an empty value or a database password that is not letters and digits (they go into database URLs). Compose requires all eight, so running `docker compose` directly without one stops with its name; nothing but `make compose-up` generates a secret.
- **Image:** `STACK_IMAGE` names the image for `migrate`, `seed` and `app` (default: built from the `Dockerfile` as `socboard-stack:local`).
- **Data directory:** `STACK_PGDATA_DIR` is where Postgres keeps its files. Unset, a named volume (`socboard_socboard-pgdata`). An absolute host path is mounted instead; it must be owned by the `postgres` user of the image (uid 999 in the official image) and may be a mount point, because Postgres uses a subdirectory, `pgdata`. The deployed server mounts its encrypted volume here (D17c).
- **A second stack on the same host (#271):** three optional variables, each defaulting to today's dev value, so with none set nothing changes. `STACK_PROJECT` (default `socboard`) is the Compose project name; it prefixes the network, the containers and the volumes. `STACK_NET` (default `10.231.77`) is the first three octets of the network's `/24`: the gateway is `.1`, the proxy `.10` and the pool for the other containers `.128/25`, and Docker refuses a second network whose subnet overlaps the first, so a second stack needs its own prefix. `STACK_TRUSTED_PROXIES` (default: the proxy's address) is the address the app trusts to forward the caller's address; on the server it is the gateway. A second stack also needs its own `STACK_APP_PORT` and `STACK_PROXY_PORT`, and its own `STACK_PGDATA_DIR` if that is a host path (the default named volume already carries the project name). `tests/test_compose.py` checks the file with the defaults and with an example second stack (`STACK_PROJECT=socboard-prod`, `STACK_NET=10.231.78`).
- **Stopping and starting over:** `make compose-down` removes the containers and the network and keeps the data. Deleting the data (`docker volume rm`) is your step. If you delete `compose.secrets` but keep the data, `db-init` cannot log in as the superuser, because Postgres set that password on its first start: restore the file or delete the data too.

### Tests against the stack

`make test-stack` runs the journeys, the refusals and the hostile-text test of `tests/test_journeys.py`, and the live checks of `tests/test_stack.py`, over HTTP against a running Compose stack (`make compose-up`). `make test` runs the same journeys in process on SQLite as before. The address of the stack is the test variable defined at the top of `tests/stack_support.py` (not a runtime setting, so it is not in the table above); for the stack it is the proxy's, `http://127.0.0.1:8080`. It must be plain `http` on `127.0.0.1`, `localhost` or `::1`, with no path, user, query or fragment, because the tests ingest alerts and claim cases; any other value stops the run with fixed text.

- **What a live run needs:** the ingest key and the three seed passwords in the environment, under the names in the table above (the stack writes them to `compose.secrets`; load them into your shell yourself and never print them), `STACK_APP_URL` (the app's own published port, `http://127.0.0.1:8000`) and `STACK_SCHEMA_FILE` (the built image's schema as JSON, made with `docker compose exec -T app python -c "import json; from socboard.app import create_app; print(json.dumps(create_app().openapi()))"`). A missing one fails naming it, and never skips.
- **What it checks:** the journeys as in process, except that two assertions that assume an empty database (journey 1's queue, journey 2's closed list) look only at the cases the test made; the forwarded-address rule (six failed logins, each with a different forged `X-Forwarded-For`, are limited on the sixth, through the proxy and on the app's own port, and a real user can still sign in; a valid ingest key is limited with a different forged address each time, and the refused alerts are not stored); and that the operations of the built image's schema equal those of `openapi.yaml`.
- **Side effects:** a run adds cases that stay (the audit trail is append-only) and ingests about 45 alerts against the default of 60 a minute for the key, so the ingest helper waits once for the limit's `Retry-After` when a second run comes within a minute. The forwarded-address tests add 10 failed logins to the client address's counter (the default limit across usernames is 20, window five minutes), so a second full run within five minutes makes the test on the app's own port, and then the ingest test, fail on a 429 when the Lead signs in; run `docker compose restart app` between runs, which clears the counters.
- **Not counted as skipped:** the tests that need a stack are marked `stack_only`; without the address they are deselected and the run header says `stack_only: N marked, N deselected`.
- **Behind the proxy:** the tests assert the bucket behaviour, not the client address the app sees. The app sees the address the proxy saw for the caller, which on a Docker host is the network's gateway, not the caller's own address (`VERIFY:` in CI; not stable, so no test pins it). `VERIFY:` that Caddy replaces a forged `X-Forwarded-For` with the caller's address, as the comment in `compose/Caddyfile` says; if CI shows otherwise, the forged-address tests fail and that is a finding.

### Browser test

`make e2e` drives the built site in a real browser against a running Compose stack (`make compose-up`), with two Analysts and a Lead. It lives in `e2e/` (Playwright, pinned in `e2e/package.json` and `e2e/bun.lock`, installed apart from the frontend's packages) and never runs in `make test` or `make verify`. The stack address is the same variable as the stack tests use (defined in `e2e/lib/stack.ts`; plain `http` on the loopback only); `make e2e` exits 2 naming it when it is unset, and fails naming `make e2e-install` when the packages are missing. It does not start the stack.

- **Once:** `make e2e-install` for the packages, then `cd e2e && bunx playwright install chromium` for the browser.
- **What a run needs:** the ingest key and the three seed passwords in the environment under the names in the table above (load them from `compose.secrets` into your shell yourself and never print them).
- **The journey:** three alerts are ingested through the API (the API is the intake), `analyst-a` sees them in the queue most severe first, claims and starts one, `analyst-b` (whose page was opened before the claim) claims the same case and gets the server's refusal (409) while the owner stays `analyst-a`, closing without a reason is not possible, `analyst-a` closes it with a reason, and the Lead reads its audit trail: the ingest, the claim, the start and the close, in that order. A hostile alert (a script tag and an image tag in the text) shows as text and runs nothing, and a wrong password shows the sign-in error and signs nobody in.
- **Requests one run makes:** 4 ingest requests (3 for the journey and 1 for the hostile-text test, of the key's 60 a minute; the first one waits up to a minute if the integration tests just used the allowance up) and 5 sign-ins, of which 1 fails (1 of the 20 failures a client address may have in five minutes). Cases are found by a unique marker in the alert text, never by counts, and the cases of a run stay (the audit trail is append-only), so a second run works. In CI the stack tests run just before it and leave about 10 failed logins on the same client address, so the journey starts with about 10 of the 20 allowed; that is still under the limit.
- **A failure:** CI keeps the screenshot and the trace for 7 days (artifact `e2e-test-results`). A trace records what the test typed and what the page sent, so CI first runs `e2e/redact_traces.py`, which replaces every value of the run's secrets file (as written, JSON-escaped and URL-encoded), every bearer token and the token of the login answer, and fails the step if one is left; it also refuses a secrets file whose value is shorter than 8 characters, and nothing is uploaded when it fails.

### Published image

CI pushes the image that the Compose job tested to the GitHub Container Registry, from a push to `main` only (`.github/workflows/publish.yml`; the decision is `_docs/decisions/2026-11-15T0915Z-image-publish.md`). Nothing else pushes: a pull request, a fork or another branch never reaches the publishing job, and the workflow's default permissions are `contents: read`.

- **Name and tag:** `ghcr.io/<owner>/socboard:YYYYMMDD-HHMMSS-<7 characters of the commit>`, with the time in UTC taken from the commit itself, so re-running a commit gives the same tag. There is no `latest`. Deploy by the digest (`sha256:...`), which the job prints in its summary and returns as an output.
- **What is pushed:** the image the integration and browser tests ran against, saved by the Compose job and loaded by the publishing job, never rebuilt. The job compares image ids before and after the push (it pulls the image back by digest). The image's labels carry the source repository, the commit (`org.opencontainers.image.revision`) and its time (`created`), set at build time from `GIT_COMMIT` and `BUILD_DATE` (`compose.yaml`; a local build says `unknown`).
- **When:** only after every workflow run of that commit has completed with `success`: `scripts/publish_gate.py` decides from the commit's runs. The Terraform check counts when the commit ran it.
- **Credentials:** the workflow's own token, with `packages: write` on the publishing job only; no personal access token, no repository secret, no cloud role. The publishing job runs no code from the repository, and its actions are pinned by commit.
- **By hand, once:** the first push creates the package, which GitHub may make private. Set it to public in the package's settings and link it to the repository, then check `docker pull` of the tag from a machine that is not logged in to the registry; the server has no registry login.

### Deploy to dev

After a push to `main` is published, CI deploys that digest to dev (`.github/workflows/deploy-dev.yml`, logic in `scripts/ci_deploy.py`; the decision is `_docs/decisions/2026-11-15T2100Z-deploy-dev-from-ci.md`). Dev and prod are two Compose stacks on one server; this workflow deploys dev only.

- **When:** the workflow runs every time `socboard publish` completes. Its `find` job, which holds no cloud role, deploys only when that run was a push to `main` of this repository, published an image (it left the artifact `published-image`), and its commit is still the head of `main`. Otherwise it ends green saying "this publish run published nothing" or "superseded: main has moved on". A pull request, a fork or another branch never reaches the `deploy` job.
- **How:** the `deploy` job runs in the `deploy` Environment, assumes the deploy role by OIDC (secret `AWS_DEPLOY_ROLE_ARN`, no stored key), and sends the Systems Manager document `socboard-deploy` with the digest as its one parameter. It waits up to 20 minutes and reads the result by the deploy script's fixed lines: `socboard-deploy: deployed.` or `socboard-deploy: already deployed. Nothing was changed.` pass; any other status, response code or output fails. Then it checks `https://<host>/api/health` from the runner (200, up to 6 tries). Only the status, the response code and the script's fixed `socboard-deploy:` lines reach the public log; the rest of the output stays in Systems Manager.
- **The two Environment variables (set by hand):** `DEPLOY_INSTANCE_ID` (the server's instance id, the `instance_id` output of `terraform/server/`) and `DEPLOY_URL` (`https://` and the host name, no path). A missing or malformed one fails the job before any role is assumed. The instance is replaced when its bootstrap changes, which makes `DEPLOY_INSTANCE_ID` stale: the next deploy then fails at the send step saying the id may be stale, and the variable must be updated.
- **The record:** a passing deploy uploads the artifact `deploy-record-dev`, one JSON object with `schema`, `environment` (`dev`), `result`, `image`, `digest`, `tag`, `revision`, `deployed_at`, `run_id`, `run_url` and `command_id`, kept 90 days. A failed deploy writes none, so the newest record is the last good deploy. The promote workflow finds it with `GET /repos/<owner>/socboard/actions/artifacts?name=deploy-record-dev` (newest first) and downloads it by its run. A dev stack unchanged for 90 days has no record; re-running the last deploy run writes a fresh one ("already deployed").

## Currently Out of Scope

Real log sources, real customer data, automated response actions and any AI-generated verdicts.

## Planned Future Development

A LangGraph triage step that suggests severity and a summary for an analyst to confirm, n8n for intake and escalation, and an MCP server for querying cases and checking a vulnerability tracker for known exposures.