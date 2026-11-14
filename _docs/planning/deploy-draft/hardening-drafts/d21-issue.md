Title: A post-deploy smoke check that runs on the server and, for the health route, from outside

## Why

After a deploy, CI must know the app works: the health route answers, a seeded user can log in, an alert can be ingested, and it reaches the queue within the 5 second target. The authenticated steps need the Lead's password and the ingest key, which should not be stored as GitHub secrets, so they run on the server inside the deploy command, from the same image the server runs. A check that lives in the application package ships in the image with no extra step.

## Goal

`python -m socboard.smoke <url>` (and `make smoke URL=<url>`) checks the live app, prints which step failed and exits non-zero; the full check runs on the server against loopback, and `--health-only` runs from CI against the public URL.

## Acceptance criteria
- [ ] The steps, in order, each named in the output: (1) `GET /api/health` answers 200; (2) a login as `lead` succeeds; (3) one synthetic alert is ingested with the key and the call answers 201; (4) the alert's case appears in the queue within 5 seconds of the ingest response, polled every half second; (5) the Lead can read that case's audit trail and it holds the `ingested` entry. `--health-only` runs step 1 only and needs no credential.
- [ ] The alert is clearly synthetic (a fixed title, `source` `smoke-check`, an existing alert type, the current UTC time) and the check deletes nothing. The README states that each run adds one New case to the queue.
- [ ] Credentials come from the environment only: the Lead's password from `SOCBOARD_SEED_PASSWORD_LEAD` and the key from `SOCBOARD_INGEST_API_KEY` (both already in the settings list; no new variable). A missing or too short one exits 2 naming the variable. The README says a password reset with `make set-password` must be copied into `SOCBOARD_SEED_PASSWORD_LEAD` before the next smoke check, otherwise step 2 fails.
- [ ] The check never prints a password, a key or a token, in output or failure text, and prints no response body: a failure names the step, the HTTP status and, for a refusal, the contract `code`. A test greps the output of every failing case for the password, the key and the token.
- [ ] URL rules, each refused with exit 2 before any request: a URL with embedded credentials (`user:pass@host`), a scheme other than `http` or `https`, a missing host, a query or fragment, and `http` unless the host is `localhost`, `127.0.0.1` or `::1`.
- [ ] No redirect is followed, so the key and the token are only ever sent to the origin that was given (a test: a stub answers 302 and the check fails at that step without a second request).
- [ ] Each step failing is shown against a stub server in the tests (standard library `http.server` on loopback): the output names that step and the exit code is 1. A stub that never answers in time makes the whole run stop after a total timeout of 60 seconds (constant, shortened in the test) and a single request after 10 seconds.
- [ ] A 429 from login or ingest is reported as that step failing with the code `rate_limited`, not retried (the limits of #178 and #183 count requests).
- [ ] The module uses the standard library only; `make verify` passes with its tests; the existing tests pass unchanged.
- [ ] The README describes the check, how to run it on the server (`docker run` of the same image with the two variables, against loopback) and from CI (`--health-only`).

## Out of scope
- Wiring it into the deploy command or CI (D20, D22a).
- Load or latency measurement beyond the 5 second check.
- Removing the synthetic case afterwards.

## Design

The check is `socboard/smoke.py`, not `scripts/smoke.py` as in the Deploy draft, so it is inside the image D10 builds (which copies `socboard/` and no `scripts/`) and the server needs nothing but the image. The decision for the human is under "Decisions" in the grooming comment.

## Constraints
- Files: `socboard/smoke.py` (new), `tests/test_smoke.py` (new), `README.md` (its own section and the rule text of the two variables that name `make seed` only), `socboard/settings.py` (the `rule` text of `SOCBOARD_SEED_PASSWORD_LEAD` and `SOCBOARD_INGEST_API_KEY`, no new entry). Protected, as exact diffs for the human: `Makefile` (a `smoke` target validated like `migration-new`) and the `AGENTS.md` Commands line.
- Depends on: #141 (the names) and #178 and #183 (the 429s); the image of D10 is how it reaches the server but is not needed to build or test it.
- Proposed tier: standard. Expected paths: as above.
