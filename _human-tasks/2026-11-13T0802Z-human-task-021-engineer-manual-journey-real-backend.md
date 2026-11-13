# Run the frontend against the real backend and walk the journey

> Written 2026-11-13T08:02Z by the Engineer for #21. The repository is public: no secrets here. Choose the secret and the three passwords yourself and never paste them into the issue.

## Purpose

Check the last acceptance criterion of #21 by hand: an Analyst logs in, claims a case, moves it to Investigating, adds a note and closes it with a reason; the Lead reads the audit trail and the closed-cases view; a 401 and a wrong password behave. Issue: #21. The Engineer could not run this in Kiro Web (no backend database, no browser), so the criterion is NOT CHECKED until you paste the result. The automated version is #22.

## Where to run it

The dev container or a clean clone with Python, uv, bun and make installed, plus a browser that can open http://127.0.0.1:5173/ (forward the port in a dev container). Not here: Kiro Web.

## Before you start

1. Install the packages once.

```
make install
```

Expected: both the Python and the frontend packages install without an error.

2. Set these environment variables in the shell you use for the steps (names only; pick values yourself, do not write them down in the issue): `SOCBOARD_TOKEN_SECRET` (at least 32 characters), `SOCBOARD_SEED_PASSWORD_ANALYST_A`, `SOCBOARD_SEED_PASSWORD_ANALYST_B`, `SOCBOARD_SEED_PASSWORD_LEAD` (12 characters or more each). Optional: `SOCBOARD_INGEST_API_KEY` (at least 16 characters) if you want to post an alert with Step 5. Leave `VITE_USE_MOCK` for Step 4.

## Steps

### Step 1: create the database tables

```
make migrate
```

Expected: the migrations apply; a second run changes nothing.
If different: stop, and tell the orchestrator what was printed.

### Step 2: load the users and the asset inventory

```
make seed
```

Expected: the users `analyst-a`, `analyst-b` and `lead` and the inventory are added. A missing or short password is named and nothing is added.

### Step 3: start the backend

```
make backend-run
```

Expected: it reports it is running on http://127.0.0.1:8000/ (log: `.scratch/backend.log`). It needs `SOCBOARD_TOKEN_SECRET`, or it exits 2.
Risk: leaves a process running. Stop it in Step 10.

### Step 4: start the frontend with the mock off

Set `VITE_USE_MOCK` to `false` in the shell, then run:

```
make frontend-start
```

Expected: the dev server runs on http://127.0.0.1:5173/. The page header shows no `mock` badge, and the sign-in page shows no "Mock mode - test accounts" box.
Risk: leaves a process running. Stop it in Step 10.

### Step 5: get at least one open case

The seed adds no alerts. Post one with the ingest API (`POST /api/alerts`, `X-API-Key` header, body fields `source`, `type`, `asset`, `timestamp`, `title`, `description`; see `openapi.yaml`), or use alerts already in your database. Post two if you can, so the queue has two rows.

Expected: the case shows in the queue within about 3 seconds without a reload (the queue polls every `VITE_POLL_INTERVAL_MS`, default 3000).

### Step 6: the Analyst journey

In the browser, sign in as `analyst-a` with the password you set. Then:

1. Open the queue and check the order is as the server sent it (Critical first, then the oldest alert). Open a case.
2. Click "Claim case". Expected: the owner shows Ana Alvarez (the seeded display name).
3. Click "Start investigating". Expected: the status shows Investigating.
4. Add a note. Expected: it appears in the Notes list as plain text.
5. Click "Close case...", pick a reason, confirm. Expected: the status shows Closed with the reason, and the case leaves the queue.
6. Check the page: no "Audit trail" panel and no "Closed" link in the top bar (Lead-only).
7. Check the session: in the browser developer tools, Application tab, the `socboard.session` key is in Session storage and not in Local storage.

### Step 7: the Lead journey

Log out, then sign in as `lead`. Then:

1. Open the case you closed. Expected: the Audit trail panel lists the entries oldest first: ingested (by system), claimed, status changed (twice), note added.
2. Open "Closed" in the top bar. Expected: the closed case is listed with its reason.

### Step 8: the wrong-password check

Log out, then sign in as `analyst-a` with a wrong password.

Expected: the server's message shows on the sign-in page, the page does not crash, and you stay on the sign-in page.

### Step 9: the 401 check

Sign in as `analyst-a`. Stop and restart the backend with a different `SOCBOARD_TOKEN_SECRET` (`make backend-stop`, set the new value, `make backend-run`), then wait for the next poll (about 3 seconds) or reload.

Expected: you are returned to the sign-in page and `socboard.session` is gone from Session storage.
If different: say what you saw.

### Step 10: stop what you started

```
make frontend-stop
```

```
make backend-stop
```

Expected: both report they stopped.

## Record the result

Paste on #21 (no passwords, no secret, no token): pass or fail for each step below, and anything unexpected. Commit this file with the result so a later run can compare.

| Step | Pass or fail | Output or note |
|---|---|---|
| 1 migrate | | |
| 2 seed | | |
| 3 backend-run | | |
| 4 frontend-start, no mock badge | | |
| 5 case appears by polling | | |
| 6 Analyst: claim, Investigating, note, close with reason | | |
| 6 Analyst: no audit panel, no Closed link, token in session storage only | | |
| 7 Lead: audit trail, closed-cases view | | |
| 8 wrong password message | | |
| 9 401 returns to sign-in | | |
| 10 both stopped | | |

## Risk

Two local servers run until Step 10. The database file `socboard.db` (git-ignored) holds the test data; delete it yourself if you want a clean start. Nothing is pushed.
