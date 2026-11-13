# Deploy phase: backlog proposal (draft, for the human's approval)

**Human ruling, 2026-11-13:** the recommendations in section 6 (questions 1 to 5) are approved: trigger functions for the Postgres audit rule, in-process login and ingest limits, frontend settings as build arguments, lanes A and C may run in parallel, D2 is Heavy. Region: the deployment runs in `us-east-1`; it is the human's own AWS account (home region `ap-southeast-2`) and the human has used `us-east-1` before, so no organisation policy is expected to block it. Wave 1 is filed from this list: D1 is #139, D2 is #140, D3 is #141, D4 is #142, D5 is #143 (no label added; the human approves each after grooming); D6 onward is written in full after wave 1 is under way. Spec notes A and B (section 7) are read as approved; `_docs/specs.md` is protected, so the exact diff is for the human to commit.

PM-style proposal. **No issue is filed and no roadmap, spec or decision file is changed by this document.** It reviews `roadmap-3-deploy.md` against `main` at `5d0ffa6` (Core #7 to #21 closed; #22 re-groomed but its new body not yet confirmed), proposes changes to that roadmap, drafts the issues, and states what #22 means for them. Issue bodies are written in full only after the human approves the list and answers the questions in section 6. Numbers are `D1` to `D23` until the tracker gives them real ones.

## 1. Definition of ready (`procedures/planning.md#definition-of-ready`)

The draft of 2026-11-12 (`deploy-draft/readiness-and-questions.md`) said NOT READY. The spec changes it asked for are now in `_docs/specs.md` (commit 185bb50). Item by item:

| Item | Where | Ready? |
|---|---|---|
| Problem and core assumption | spec §1 (unchanged) | Yes |
| Named users | spec §2: Operator and Visitor added | Yes |
| At least one end-to-end journey | spec §3 journey 3, "Deploy to Public URL" | Yes |
| Requirements acceptance criteria can come from | spec §4 "Deploy (Phase 2)": ten Must stories, including login and ingest limiting | Yes |
| Explicit scope boundary | spec §6 Phase 2: one environment, one server, one Postgres, AWS, Terraform | Yes |
| Non-goals (not an empty heading) | spec §7, with Deploy non-goals | Yes |
| Listed invariants | spec §5, including secrets, one owner on both databases, no long-lived cloud key | Yes, with one gap (spec note A) |

This is the PM's reading; the confirmation that counts is the PM's in the backlog-proposal comment, which the human reads before approving.

## 2. Review of `roadmap-3-deploy.md`: what is still right and what must change

Still right: the seven human decisions (2026-11-12), the one-image design, Caddy for TLS, SSM instead of SSH, OIDC with separate roles, human-only first apply, the Core-then-Deploy order, and the two-lane shape (app work and Terraform do not share files).

Must change before issues are filed:

1. **Roadmap task 4 ("Run on Postgres") is several issues, not one.**
   - The audit trail's append-only rule is three SQLite triggers (`RAISE(ABORT, ...)`) in migration `0001`; Postgres needs different trigger code. Spec §5 says the trail is append-only on any supported database.
   - `audit_entries` uses `sqlite_autoincrement`; `socboard/audit_guard.py` is written around SQLite names (`sqlite_sequence`, PRAGMAs, the SQLite dialect for its compile step).
   - `pyproject.toml` has no Postgres driver, and every test file runs on SQLite.
   - Proposed split: D5 (driver, portable migration, Postgres in CI, tests choose the database by URL) and D6 (append-only on Postgres; Heavy, security boundary, updates `_docs/decisions/2026-11-12T0834Z-data-model.md`). Roadmap task 5 (single ownership on Postgres) becomes D7.
2. **Two Must stories have no task: login attempt limiting and ingest rate limiting** (spec §4). The roadmap says no Deploy story exists for them; it does now. They are D8 and D9.
3. **Roadmap task 3 (configuration) is incomplete.**
   - It omits `SOCBOARD_TOKEN_SECRET` (the backend exits 2 without it), so it must be in the cloud secret and the Compose env file. Spec §5 lists the secrets without it (spec note A).
   - It lists "poll interval" as a runtime variable. It is `VITE_POLL_INTERVAL_MS`, fixed when the frontend is built (so are `VITE_USE_MOCK` and `VITE_FEATURE_REASSIGN`). In a one-image design they are build arguments, or the spec line "configurable via env var" must say "at build" (spec note B).
   - Fold in #104 item 5 (a 32-space secret is accepted): a public deployment should refuse a blank-looking secret.
4. **Task 7 (image) needs #100** (pin Python 3.12). The base image and CI must agree, and CI ran 3.14.
5. **Image build feedback comes too late.** The sandbox runs no Docker, so CI is the only place the image is built until the human runs it. Put a `docker build` job in CI with the Dockerfile issue (D10), not with roadmap task 11.
6. **`AGENTS.md` Layout lines** for `Dockerfile`, `docker-compose.yaml`, `terraform/` and `e2e/` are protected edits. Batch them with the Makefile approvals each issue already needs.
7. **The decision record (roadmap task 1) does not exist** in `_docs/decisions/`. It is D1, and it should also record the two new decisions in section 6 (Postgres audit approach, rate-limit design) so Heavy issues D6 and D8 have an approved record to cite.

Not changed: the Terraform tasks, the deploy script, the smoke check, CD and the runbook keep their roadmap content (renumbered D15 to D23). AWS-specific details stay marked `VERIFY:` until the human's first apply.

## 3. What #22 means for Deploy

**Short answer: it blocks little, and it should be given two small changes before its new body is confirmed.**

What the human has already decided for #22 (comment of 2026-11-13, "human decisions for the #22 re-groom"): `make run` is the Vite dev server with the mock off and `/api` proxied (option A), started in the background with pid files, `make stop` stops only what it started, `SOCBOARD_INGEST_API_KEY` is required, and the order is #120 (now closed), #114, #104, then #22. Option C (the backend serves the built frontend) was not chosen, so **#22 does not do D2**. `make run` stays a dev-mode command on SQLite, loopback only. It is not the deployed shape.

| Deploy item | Needs #22? | Why |
|---|---|---|
| D1 to D11, D15 to D19 | No | Different files (`socboard/`, `terraform/`, Dockerfile). They can start now. |
| D12 (integration tests against Compose), D13 (E2E) | Yes | They reuse #22's journeys and schema check. |
| D11 (Compose), D20 (first deploy) | Yes, loosely | The one-shot migrate and seed step mirrors `make run`'s migrate, seed and preflight. |

Changes to ask for in #22's body before the human confirms it (it is not yet confirmed, so this costs nothing):

1. **Make the journey tests portable.** `tests/test_journeys.py` should take its client from one fixture that is in-process by default and can point at a live base URL when `SOCBOARD_BASE_URL` is set. Without that, D12 rewrites the journeys. The fixture is a few lines; the journeys must then avoid touching the database directly.
2. **Keep one list of environment variable names.** #22's `scripts/run_preflight.py` lists five required names, README repeats them, and D3, the Compose env file and the cloud secret will need the same list. Name the list once (the preflight's list is fine) and have the README test check it, as #22 already proposes. D3 reuses it.
3. **Say in the README that `make run` is the development command.** Name the Compose target differently (`make compose-up`, in D11) so the two cannot be confused.

Things that are fine as written: the schema check (`create_app().openapi()` against `openapi.yaml`, by method, path and `operationId`) is exactly what CI should keep running against the built image; the refusal tests and hostile-text tests carry over unchanged.

Effects of later Deploy work on #22's tests: login limiting (D8) can answer 429 to repeated wrong passwords in tests. The spec says limits are set by configuration, so the test settings raise them; D8 must provide that.

Related, not blocking: #104 (before #22, by the human's order) touches `make seed` and the `seed` help line; it also helps D11's one-shot seed step. #131 (frontend follow-ups from #21, after #22, before Deploy) includes the case-page action buttons; do it **before D13**, so the browser test does not pin a screen that is about to change. #110 changes what the `ingested` audit entry stores; D12 and D13 must not pin that text either.

## 4. Order and lanes

```
Lane A (app):       D2  D3  D4 -> D5 -> D6 -> D7 -> D10 -> D11 -> D12 -> D13 -> D14
Lane B (hardening):          D8, D9 (after D3; touch auth.py and ingest.py)
Lane C (infra):     D1 -> D15 -> D16, D17 (parallel) -> D18 -> D19 (human)
Join:                                   D19 + D10 + D11 -> D20 -> D21 -> D22 -> D23
```

- Lane C touches only `terraform/`, so it conflicts with nothing in A or B. Start D1 first because AWS account and budget setup depend on the human, and that has the longest wait.
- `delivery-pace.md` says never two Engineer launches on Core issues at once. Lanes A and C use disjoint files; the human decides whether to relax the rule for Deploy (question 4).
- Do D5 before D6 and D7. D5 and D8 and D9 do not overlap in files, except `socboard/auth.py` between D3, D8 and #104; serialize those.
- Human needed: D1 (approve), D4 and D10 to D11 (protected files; one local Docker check), D15 to D17 (apply), D19, D20 (first run), D22 (variables, environment approval), D23 (teardown).

## 5. Issue drafts

Full bodies for wave 1 (D1 to D5) are in `_docs/planning/deploy-draft/wave-1-issue-drafts.md`; the later ones are written in full after the human approves this list.

Tier is proposed, not final. Model: **S** stronger, **M** middle, **W** weaker (suitability, as in the earlier plan). "Roadmap" is the old task number in `roadmap-3-deploy.md`.

### Lane A: the application

**D1 Record the deploy decision (Heavy, docs; roadmap 1).** Who: PM drafts, human approves. Model S.
- A record in `_docs/decisions/` holds the seven decisions of 2026-11-12 with reasons, the region (deployment `us-east-1`; the human's own account, home region `ap-southeast-2`), and the accepted risks, plus the two decisions of section 6 (Postgres audit approach, rate-limit design). Status line `approved` only after the human says so; `make decisions` lists it.
- It states what Terraform state and Secrets Manager hold and what they must never hold.

**D2 Serve the built frontend from FastAPI (Heavy: a new interface; roadmap 2).** Model M.
- Depends on: nothing open (#21 closed). `make frontend-build` output is the source.
- With `frontend/dist` present, `GET /` and a screen route such as `/cases/1` return the entry page; a path under `/api` that matches no route still returns the JSON 404 `not_found`, never the entry page (a test for each).
- Without `frontend/dist`, the app starts and every API route behaves as before; `make backend-run` is unchanged.
- No change to `openapi.yaml`, and the generated schema is the same (the existing comparison test passes).
- Static files cannot be used to read outside `dist` (a traversal test).

**D3 Deployed-mode configuration (Standard; roadmap 3).** Model M. Depends on: the variable list from #22, #104.
- One place lists every runtime variable (database URL, token secret, ingest key, three seed passwords, host and port); the README and a test read it.
- A single explicit setting turns on deployed mode (name chosen at grooming). In deployed mode a missing or blank secret stops start-up with a message naming the variable, never its value, exit 2 (one test per variable).
- The token secret and the ingest key are refused when they are all spaces or too short (folds in #104 item 5).
- `.env.example` lists names only. Build-time settings (`VITE_*`) are documented as build arguments.

**D4 Let the container bind a non-loopback address, safely (Heavy: a security limit; roadmap 6).** Model M. Protected: `AGENTS.md` line.
- `make backend-run` still refuses any host except loopback (existing test stays).
- A separate, explicit opt-in that only the image sets allows `0.0.0.0`; without it, `SOCBOARD_HOST=0.0.0.0` still exits 2 (a test).
- The refusal message and the `AGENTS.md` gotcha are updated to say who may opt in.

**D5 Run on Postgres: driver, portable schema, CI service (Heavy: data model; roadmap 4, first part).** Model S.
- A dependency approval for the driver comes first (`uv add`, lockfile committed).
- `SOCBOARD_DATABASE_URL` chooses the database; SQLite stays the local default; `make migrate` and `make seed` work on both.
- Migration `0001` runs on Postgres with the same tables, constraints and ids (`audit_entries` ids keep ascending, with no gaps promised).
- CI runs the backend tests against a Postgres service container; the test fixtures take the database from the URL. The Engineer cannot run Postgres in the sandbox, so CI is the evidence.
- Known failing SQLite-only tests (trigger tests) are named and handled by D6, not skipped silently: `make verify` reports no new skips.

**D6 Append-only audit trail on Postgres (Heavy: security boundary; roadmap 4, second part).** Model S. Depends on: D5, D1 (approved record).
- On Postgres an UPDATE, DELETE or overwrite of an `audit_entries` row is refused by the database (one test per case, mirroring `tests/test_data_audit_triggers.py`), and `append_audit` still works.
- `socboard/audit_guard.py` is reviewed for SQLite-only parts; each stays, is made dialect-aware, or is recorded as an open limit. The data-model record states the Postgres limits (for example what a superuser can still do).
- The SQLite behaviour and its tests are unchanged.

**D7 Prove single ownership on Postgres (Standard; roadmap 5).** Model M. Depends on: D5.
- Two simultaneous claims of one case leave exactly one owner and one `claimed` audit entry on Postgres (a test using real concurrent connections, repeated enough to catch a race).
- The loser gets the contract's 409; nothing about the winner's state changes.

**D8 Login attempt limiting (Heavy: authentication; new, spec §4).** Model S. Depends on: D3, D1. Open decision: section 6, question 2.
- Repeated failed logins for one username, or from one client, are refused or slowed for a time with the fixed answer the spec names, and the answer does not reveal whether the username exists (a test comparing a real and a made-up username).
- A correct login still works for another user while one is limited; the limits and window come from configuration, with defaults chosen in the issue and stated in README.
- The limit works behind a reverse proxy: how the client address is read is stated, and a forged forwarding header from outside cannot choose another client's bucket.
- `openapi.yaml` documents the new response; `make contract-check` passes.

**D9 Ingest rate limiting (Standard; new, spec §4).** Model M. Depends on: D3.
- A source that exceeds the configured rate gets 429; the excess alerts are not stored (a test counts rows). A source within the rate is unaffected; two sources have separate limits.
- The refusal order of `openapi.yaml` is kept (401 for a wrong key stays first); the response is documented and `make contract-check` passes.

**D10 One container image, and a CI build job (Standard; roadmap 7).** Model M. Depends on: D2, D3, D4, #100. Protected: `Dockerfile` Layout line in `AGENTS.md`.
- A multi-stage `Dockerfile` builds the frontend (`VITE_USE_MOCK` false, build arguments for the `VITE_*` settings), then copies `dist` into a non-root Python 3.12 image; `.dockerignore` keeps `.env`, `.scratch/` and `node_modules` out.
- CI builds the image on every pull request and fails on a build error; the human runs it once locally and reports the result.
- No secret, `.env` file or demo credential is in the image or the bundle (a check lists the image's files, as `frontend-build` does for the bundle).

**D11 Compose stack (Standard; roadmap 8).** Model M. Depends on: D5, D10, #22. Protected: `Makefile`.
- `make compose-up` and `make compose-down` start and stop the app and Postgres; Postgres has a health check and a named volume; a one-shot step migrates and seeds before the app starts; the app waits for it.
- Required variables come from the single list (D3); starting with one missing fails naming it.
- The app port is published on the host's loopback only.

**D12 Integration tests against the Compose stack (Standard; roadmap 9).** Model M. Depends on: D11, #22.
- #22's journeys run against `SOCBOARD_BASE_URL` unchanged: ingest to queue, login, claim, move, note, close, audit read, and the refusals.
- The check that the running app serves what `openapi.yaml` says (method, path and `operationId`) runs against the stack.
- Nothing is pinned that #110 may change (the `ingested` audit text).

**D13 End-to-end browser test (Standard; roadmap 10).** Model S. Depends on: D11, #131 (case-page buttons). Dependency approval for the browser tool.
- A browser test with two Analysts and a Lead: ingest, claim, a refused second claim, close with a reason, the Lead reads the audit trail.
- Runs in CI against the Compose stack with no real credentials; failure leaves a screenshot or trace as an artifact.

**D14 CI: build, integration and E2E jobs (Standard; roadmap 11).** Model M. Protected: `.github/workflows/verify.yml`.
- On every push to `main`: unit tests in parallel, then image build, then integration and E2E; a failure at any step stops the later ones.
- Pull requests run the same checks and never receive a deploy credential. `make verify` is unchanged.

### Lane C: infrastructure (Eng writes; the human applies)

All use `VERIFY:` markers for unverified AWS details; CI runs `fmt` and `validate`; the sandbox cannot run `plan`. A new top-level `terraform/` folder and its `AGENTS.md` Layout line need the human's approval.

**D15 Terraform: state bucket (Standard; roadmap 12).** Model W-M. Depends on: D1.
- A private, versioned, encrypted bucket; `.gitignore` covers state and real variable files; an example variable file is tracked; the steps to move state into the bucket are written down.

**D16 Terraform: CI access (Heavy: IAM; roadmap 13).** Model S. Depends on: D1, D15.
- OIDC provider and three roles (plan read-only, apply, deploy), each trusted for this repository's numeric ids and a named GitHub Environment; no stored keys; the deploy role may only send the one Systems Manager command. The human applies it; CI cannot change its own roles.

**D17 Terraform: server (Standard; roadmap 14).** Model M. Depends on: D1, D15.
- One instance with no key pair, an Elastic IP, a security group for 80 and 443 only, an instance role (Systems Manager plus read of the one named secret), an encrypted data volume with daily snapshots, a bootstrap that installs Docker, Compose and Caddy, budget notifications at three thresholds, and the `project=socboard` tag on every resource.

**D18 CI: Terraform checks and plan (Standard; roadmap 15).** Model M. Depends on: D15 to D17. Protected: workflow.
- Pull requests that touch `terraform/` run `fmt -check`, `init -backend=false` and `validate` without credentials, then a read-only `plan` shown on the pull request; forks get no role. The Terraform version is pinned.

**D19 First apply, by the human (checklist; roadmap 16).** Who: human; Eng drafts the checklist.
- Before any apply: check that the provider and the state backend both name `us-east-1` (the account's home region is `ap-southeast-2`, so a default region setting in the AWS CLI or console can point elsewhere). Then account and budget notifications, GitHub Environments (`infra` needs a named reviewer and administrators not exempt), the three applies in order, the secret created, the IP and hostname noted, and a second `plan` shows no changes.

### Join

**D20 Deploy script and first deploy (Standard; roadmap 17).** Model M. Depends on: D10, D11, D19, #22.
- A script run through Systems Manager pulls the image by tag, reads the secret with the instance role, writes the env file with restrictive permissions, runs Compose and reloads Caddy; if the image cannot be fetched, the current release keeps serving. The human runs it once and the HTTPS URL loads.

**D21 Post-deploy smoke check (Standard; roadmap 18).** Model W-M. Depends on: D3.
- `make smoke URL=<url>` checks `/api/health`, a login, one ingest, and that the alert is in the queue within 5 seconds; it exits non-zero with a message naming the failed step. It reads credentials from the environment only.

**D22 Deploy from CI on a green push to `main` (Heavy: deploy credentials; roadmap 19).** Model S. Depends on: D14, D18, D20, D21.
- Deploy runs only after every test passes, with the OIDC deploy role; the job summary records the image tag and digest; the smoke check runs after; a failed smoke check fails the job. Terraform apply from CI covers only the server part, only on `main`, after the named reviewer approves.

**D23 Deploy and teardown runbook (Standard, docs; roadmap 20).** Model W-M. Depends on: D19, D22.
- How to deploy, where the URL is, how reviewers get credentials (not in the README), how to restore Postgres from a snapshot, and the ordered deletion of every cloud resource (server, then CI access, then snapshots and secrets, state bucket last).

## 6. Open questions for the human (each with a recommendation)

1. **How does Postgres keep the audit trail append-only?** (a) Trigger functions in the migration, the same rule as SQLite triggers. (b) A database role that has no UPDATE or DELETE right on `audit_entries`. (c) Both. Recommendation: (a) now, record (b) as a Phase 3 hardening; either way the record states what the database owner can still do. Heavy; goes in D1 and D6.
2. **Rate-limit design (D8, D9).** (a) In-process counters: one server and one container make this enough, and it is the smallest change; limits reset on restart and do not work across replicas. (b) Counters in the database: survives restarts, more code and writes. Defaults to choose in the issues (attempts, window, ingest rate). Recommendation: (a), with the limits recorded as a known limit; how to read the client address behind Caddy decided in D8.
3. **Frontend settings at build time.** Pass `VITE_POLL_INTERVAL_MS`, `VITE_FEATURE_REASSIGN` and `VITE_USE_MOCK` as image build arguments, or make the app serve a small runtime settings file? Recommendation: build arguments (no new interface), and change the spec line to say "configurable at build".
4. **May lanes A and C run in parallel,** relaxing the "never two Engineer launches on Core issues at once" rule for Deploy? Recommendation: yes; their files are disjoint (`socboard/`, `tests/` and `Dockerfile` versus `terraform/`).
5. **Where does D2 sit on the tier scale?** #22's PM treated "the backend serves the build" as a new interface, which would make it Heavy. Recommendation: Heavy, as above.

## 7. Spec notes to propose (the human decides; `_docs/specs.md` is protected)

```
Spec note A: the secrets invariant omits the token signing secret
- Added: in §5 "Secrets come only from the environment", add "the token signing secret" to the list (ingest key, seeded passwords, database password).
- Why: SOCBOARD_TOKEN_SECRET is required at start-up (#13); on a public deployment it must come from the cloud secret store like the others.
```

```
Spec note B: the poll interval is set when the frontend is built
- Changed: §5 "(configurable via env var)" to "(configurable when the frontend is built)", unless question 3 chooses a runtime settings file.
- Why: the interval is VITE_POLL_INTERVAL_MS, read at build time, so a single image carries one value.
```

## 8. What this proposal does not cover

Phase 3 (DevOps: separate dev and prod, observability, alerting, the on-call agent) is outlined in the spec and given its own roadmap when Deploy is done. The Terraform and AWS details here follow the roadmap and have not been verified against AWS; the human's first apply is the first real check.
