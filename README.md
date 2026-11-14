# SOCBoard

SOCBoard is a web-based case management board for a security operations centre (SOC). Alerts arrive through an API, receive a severity from fixed rules, and appear in a shared analyst queue. Analysts claim an alert, investigate it and move it through a defined workflow to closure. Every status change, assignment and note is recorded in an audit trail.

All data is synthetic. The graded version uses no machine learning in its critical path, so every severity and every status change can be explained and tested.

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

### Runtime variables

Every runtime variable, from the one list in `socboard/settings.py` (a test fails if this table and that list disagree on a name or on the Secret and Required in deployed mode columns). The example env file at the repository root has the same names, with no values. Never put a value in this file, a commit or an issue.

| Variable | Secret | Required in deployed mode | Rule |
|---|---|---|---|
| `SOCBOARD_TOKEN_SECRET` | yes | yes | At least 32 characters, not counting leading and trailing spaces; required in every mode. In deployed mode it must also not be one repeated character, whitespace ignored (so `s s s ...` is refused). |
| `SOCBOARD_SEED_PASSWORD_ANALYST_A` | yes | no (the seed step needs it) | At least 12 characters; read by `make seed` only. |
| `SOCBOARD_SEED_PASSWORD_ANALYST_B` | yes | no (the seed step needs it) | At least 12 characters; read by `make seed` only. |
| `SOCBOARD_SEED_PASSWORD_LEAD` | yes | no (the seed step needs it) | At least 12 characters; read by `make seed` only. |
| `SOCBOARD_INGEST_API_KEY` | yes | yes | At least 16 characters; without it ingest is closed and one warning is logged. In deployed mode it is required: at least 16 characters, no leading or trailing whitespace (HTTP strips it from the header, so such a key could never match), and not one repeated character, whitespace ignored. |
| `SOCBOARD_DATABASE_URL` | yes | yes | A valid database URL; default `socboard.db` in the repository root. In deployed mode it must be set. |
| `SOCBOARD_LOGIN_MAX_FAILURES` | no | no | Failed logins allowed per client address and username within the window before login answers 429; a whole number of 1 or more (default 5). See "Login attempt limits". |
| `SOCBOARD_LOGIN_CLIENT_MAX_FAILURES` | no | no | Failed logins allowed per client address, across all usernames, within the window; a whole number of 1 or more (default 20). |
| `SOCBOARD_LOGIN_WINDOW_SECONDS` | no | no | The length of the window, in seconds; a whole number of 1 or more (default 300). |
| `SOCBOARD_LOGIN_MAX_TRACKED` | no | no | The most (client, username) and client counters kept at once; the least recently failed is dropped first; a whole number of 1 or more (default 10000). |
| `SOCBOARD_TRUSTED_PROXIES` | no | no | A comma-separated list of IP addresses of reverse proxies whose `X-Forwarded-For` header is believed (default none: the header is ignored). See "Login attempt limits". |
| `SOCBOARD_HOST` | no | no | `127.0.0.1`, `localhost` or `::1` (default `127.0.0.1`); see "Binding beyond localhost". |
| `SOCBOARD_PORT` | no | no | A whole number from 1 to 65535 (default 8000). |
| `SOCBOARD_STATIC_DIR` | no | no | The built frontend folder (default `frontend/dist`); see "Serving the built site". |
| `SOCBOARD_MODE` | no | no | Exactly `deployed` turns deployed mode on. Any other value, including an empty one or a different case, leaves it off. `make run`, `make backend-run` and the tests never set it. |

Deployed mode: with `SOCBOARD_MODE` set to `deployed`, the backend refuses to start (exit code 2) when the token secret, the ingest key or the database URL is missing, empty, too short or made only of whitespace, when the secret or the key is one repeated character (whitespace ignored), or when the key has leading or trailing whitespace. The message names the variable and never prints its value. Outside deployed mode nothing else changes.

Login attempt limits: `POST /api/login` counts failed logins per client address and username, and per client address across usernames. At the limit it answers 429 `rate_limited` with a `Retry-After` header until enough old failures leave the window; a real and a made-up username get the same answer. The check runs before any password work. A successful login counts nothing. Because the three seeded usernames are public, the counters are keyed on the client address too: an outsider can only limit their own address, never a reviewer signing in from another one. The counters live in the process, assume one worker, and reset on restart; a second worker would multiply the limits. The client address is the socket peer. `X-Forwarded-For` is read only when the peer is listed in `SOCBOARD_TRUSTED_PROXIES`, and then the address is the rightmost entry that is not itself a listed proxy, so a forged header cannot choose another client's bucket. Behind a reverse proxy, list the proxy's address: left empty, every client shares the proxy's address, and one outsider's failures would refuse every login for a window. The server (uvicorn) has its own list, `FORWARDED_ALLOW_IPS` (default `127.0.0.1`): when the connection comes from an address in it, uvicorn replaces the peer address with the one from the forwarded header before the app sees it. Two cases follow. A proxy on the same host connects from `127.0.0.1`, uvicorn already supplies the client address, the app then sees an untrusted peer and ignores the header, and `SOCBOARD_TRUSTED_PROXIES` stays empty. A proxy on another address (a container, say) is not in uvicorn's default list, so the app reads the header itself and the proxy's address goes in `SOCBOARD_TRUSTED_PROXIES`. The image's start command must leave `FORWARDED_ALLOW_IPS` at its default or list only the proxy's address, never `*`: with `*` uvicorn believes any caller's header, and a forged one would choose its bucket. An IPv4-mapped IPv6 peer (`::ffff:a.b.c.d`) is treated as the IPv4 address.

Build arguments, not runtime variables: `VITE_API_BASE_URL`, `VITE_FEATURE_REASSIGN`, `VITE_POLL_INTERVAL_MS` and `VITE_USE_MOCK` are fixed when the frontend is built (`make frontend-build`), so changing them later needs a rebuild.

## Currently Out of Scope

Real log sources, real customer data, automated response actions and any AI-generated verdicts.

## Planned Future Development

A LangGraph triage step that suggests severity and a summary for an analyst to confirm, n8n for intake and escalation, and an MCP server for querying cases and checking VulnTriage for known exposures.