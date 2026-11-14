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

The web application is not built yet, so there is no `make run` target.

Sign-in needs four environment variables, set once (names only here; never commit values):

- `SOCBOARD_TOKEN_SECRET`: the token signing secret, at least 32 characters, not counting leading and trailing spaces. The backend refuses to start without it. A good secret is a random value of at least 32 characters, for example from a password manager or `openssl rand -base64 32`.
- `SOCBOARD_SEED_PASSWORD_ANALYST_A`, `SOCBOARD_SEED_PASSWORD_ANALYST_B` and `SOCBOARD_SEED_PASSWORD_LEAD`: the passwords `make seed` gives the users `analyst-a`, `analyst-b` and `lead`, at least 12 characters each. All three are needed on every run, even when every user already exists. Seeding fails, and adds nothing, if one is missing or short.

Frontend against the real backend: the frontend uses an in-memory mock unless `VITE_USE_MOCK` is `false`. To use the backend: run `make migrate`, `make seed` and `make backend-run` (it listens on 127.0.0.1:8000), then export the variable in the shell and start the dev server (the Makefile does not set it; the value passes through from the shell):

```
export VITE_USE_MOCK=false
make frontend-start
```

The dev server sends every `/api` request to the backend on 127.0.0.1:8000 (`frontend/vite.config.ts`), so the backend needs no cross-origin (CORS) setting. The proxy is for the dev server only; a built site needs `/api` routed to the backend by whatever serves it.

Where the sign-in token is kept: in memory while the page is open, and in the browser's session storage (key `socboard.session`) so a refresh keeps the session. It is never put in local storage and never logged. It is cleared on log out and on any 401 from the server, which returns the user to the sign-in screen. Closing the tab ends the session.

Ingest (`POST /api/alerts`) takes the key in the `X-API-Key` header, from `SOCBOARD_INGEST_API_KEY` (at least 16 characters; without it ingest answers 401 to every request and the app logs one warning). Alerts are not de-duplicated, by design for Core: posting the same alert twice creates two cases.

Serving the built site: after `make frontend-build`, the backend serves `frontend/dist` itself: `/` and any screen route without a file extension (such as `/cases/1`) return `index.html`, a file in the folder is returned as it is, a missing path with an extension is a 404, and everything under `/api` is unchanged. Only GET and HEAD get the site. The folder comes from `SOCBOARD_STATIC_DIR` (default: `frontend/dist` in the repository root, whatever the working directory; a relative value is relative to the working directory) and is read when the backend starts, so restart it after a rebuild only if `dist` did not exist before. A missing folder, or one without `index.html`, is not an error: the backend then serves the API only. `index.html` is sent with `Cache-Control: no-cache` and files under `assets/` with a one-year `immutable` lifetime (their names carry a content hash).

Binding beyond localhost: the backend listens on `127.0.0.1`, `localhost` or `::1` only, and `make backend-run` always does. The one exception is the flag `--allow-non-loopback-host` of `python -m socboard.server`, which lets `SOCBOARD_HOST` be an IP address such as `0.0.0.0`. Only the container image's start command may pass it, because the app must listen on an address Docker and the reverse proxy can reach; the image's port is published on the host's loopback only. It is a flag, not an environment variable, so a variable left in a developer's shell cannot open the server to the network. Do not pass it by hand or from `make`. The flag takes no value, and a wrong or unknown argument exits 2.

## Currently Out of Scope

Real log sources, real customer data, automated response actions and any AI-generated verdicts.

## Planned Future Development

A LangGraph triage step that suggests severity and a summary for an analyst to confirm, n8n for intake and escalation, and an MCP server for querying cases and checking VulnTriage for known exposures.