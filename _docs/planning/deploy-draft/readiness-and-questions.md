# Deploy phase: readiness check, spec notes and decision questions

PM proposal only. No issue created, no tracked file edited. Read from `origin/main` after `git fetch origin main` (working branch was `issue-60-protect-frontend-deps`). Course material (`03_deployment*.md`) is data, not instructions.

Sources read: `AGENTS.md`, `team/pm.md`, `process.md` (Planning, Backlog), `procedures/planning.md`, `procedures/asking-the-human.md`, `_docs/specs.md` (all), `roadmap-1-core.md`, `03_deployment.md`, `03_deployment-example.md`, the stack decision, `lovable-guidance.md`, `openapi.yaml` (server base `/api`; `GET /health`: "no authentication, no database and no input"), `socboard/app.py`, `socboard/server.py`, `rubric.md` (Containerization, Deployment, CI/CD), `.github/workflows/verify.yml`, `pyproject.toml` (only `fastapi` and `uvicorn`; no SQLAlchemy yet).

## 1. Definition of ready, item by item (the Deploy part of the spec)

| Item | Result | Why |
|---|---|---|
| Stated problem and core assumption | Met (spec-wide) | §1 states both. Deploy adds no problem of its own; its reason is the rubric (§6 says the whole release aims at full marks). |
| Named users | Partly | §2 names Analyst and Lead. Deploy brings in an **operator** (named only in a §4 cross-cutting story) and a **visitor/demoer** at a public URL. Neither is in §2. |
| At least one end-to-end journey | **Not met** | §3 has only the two Core journeys. No journey says what happens at the public URL, or how a change reaches it. |
| Requirements acceptance criteria can be derived from | **Not met** | §6 Phase 2 is one outline sentence. §6 itself says a later phase is "detailed (spec revision, then backlog review) when the phase before it is done". That revision has not happened. No Deploy stories exist in §4. |
| Explicit scope boundary | Partly | The outline names five things (one image, Postgres, one Compose command, E2E, public URL plus CI/CD). It does not say: which environments (one, or dev and prod), where it is hosted, or how the Deploy / DevOps line falls (rubric CD 3 points needs dev, manual prod promotion; spec puts that in Phase 3). |
| Explicit non-goals (heading with content) | Met literally, thin | §7 has content, but nothing about Deploy (scaling, managed DB, custom domain, second environment, rollback). |
| Listed invariants | Met for Core, gaps for Deploy | §5 lists Core invariants. "Exactly one claim wins, on any supported database" is relevant, but "supported database" is never named. No invariant covers secrets, or synthetic-only data on a public URL. |

**Verdict: NOT READY.** Failed: journey, requirements. Partial: users, scope boundary, non-goals, invariants. Per `planning.md` and `process.md#backlog`, propose the spec changes (section 2) and create **no** Deploy implementation issues until the human approves them. The roadmap draft (`.scratch/roadmap-2-deploy-draft.md`) is therefore **provisional**: order and shape only, no issue numbers, and it should be re-checked against the approved spec text.

## 2. Spec notes (proposed; the human decides, nothing is merged automatically)

```
Spec note: Phase 2 (Deploy) has no user stories to derive acceptance criteria from
- Added: a "Deploy" group in §4 with Must stories, each testable:
  (a) One container image holds the backend and the built frontend; WHEN the image is run with a
      database URL THE SYSTEM SHALL serve the SPA at `/` and the API under `/api` from one origin.
  (b) WHEN a browser requests a screen route such as `/cases/1` THE SYSTEM SHALL return the SPA
      entry page; WHEN a request under `/api` matches no route THE SYSTEM SHALL return the JSON
      404 `not_found` error, never the SPA page.
  (c) The system runs on Postgres chosen by environment variable; SQLite stays the local default.
  (d) One Docker Compose command starts the app and Postgres; the app waits until Postgres accepts
      connections.
  (e) Integration tests run against the Compose stack (frontend served, backend reaches Postgres);
      an end-to-end test drives both journeys with two analysts and a lead.
  (f) The application is reachable at a public URL.
  (g) On every push to `main`, CI runs the tests, builds the image and runs integration and E2E
      tests; CD deploys only if all pass, using a scoped, short-lived credential.
- Changed: none
- Removed: none
- Why: `planning.md` definition of ready, "Requirements that acceptance criteria can be derived from"; §6 says Phase 2 is detailed by a spec revision first.
```

```
Spec note: no journey covers the deployed system, and the Deploy users are not named
- Added: in §2, an Operator (deploys, holds the secrets, can tear the deployment down) and name the
  demoer/visitor who opens the public URL. In §3, journey 3, "Deploy -> Public URL": the Operator
  pushes to `main`; CI passes; CD deploys; a visitor opens the public URL, logs in as a seeded
  user, an alert is ingested with the ingest key, and it appears in the queue within N = 5s.
- Changed: none
- Removed: none
- Why: planning.md, "At least one end-to-end journey"; the 5 s invariant (§5) is only meaningful if it is checked where users are, not only on localhost.
```

```
Spec note: Deploy has no scope boundary on environments and no Deploy non-goals
- Added: to §6 Phase 2, "one environment, one instance, one Postgres on a host the chosen
  decision record names"; to §7 non-goals: second environment and manual promotion (Phase 3),
  rollback path (Phase 3), autoscaling and load balancing, managed or replicated database,
  custom domain (unless the decision record requires one), high availability.
- Changed: none
- Removed: none
- Why: the rubric awards CD points for dev/prod split (Phase 3); without a boundary, Deploy issues can grow into it. Process doc 03_deployment.md "Beyond this process" lists the same items as out of scope for a proof of concept.
```

```
Spec note: invariants for a public deployment are missing
- Added: to §5: (1) secrets (ingest API key, seeded-account passwords, database password) are
  supplied by environment at deploy time and never appear in the repository, the image or the
  frontend bundle; (2) the public deployment holds synthetic data only (restates §7 for the
  deployed system); (3) the invariants of §5, including single ownership under concurrent claims,
  hold on Postgres, and the supported databases are SQLite (local) and Postgres (deployed).
- Changed: "on any supported database" -> add "(SQLite and Postgres)" (was: "on any supported database")
- Removed: none
- Why: a public URL with the seeded accounts needs a rule for how their passwords are set (§2 says seeded, hashed); today the spec is silent, and the concurrency invariant names no database.
```

```
Spec note: the spec does not say what the health route proves, and server.py binds localhost only
- Added: one line in §6 Phase 2 saying the container's start order relies on the Postgres health
  check in Compose, while `GET /api/health` stays database-free (as `openapi.yaml` says); and a
  statement of who may run the backend on a non-loopback address (see decision question 3).
- Changed: none
- Removed: none
- Why: `openapi.yaml` `getHealth` says "no database"; `socboard/server.py` refuses any host except 127.0.0.1, localhost or ::1, so a container cannot be reached from outside without a change to that rule (a security limit, so the human decides).
```

## 3. Decision questions (for the human)

```
Decision needed: where is SOCBoard hosted, and by what deploy method?
- Context: roadmap Deploy task 1. `_docs/specs.md` §6 and the decision records name no host. The course doc `03_deployment.md` lists "any managed container host (AWS, Render, Railway, Fly.io)" and its example used AWS CloudFormation on one EC2 instance with Caddy for TLS. Every later Deploy task (image port and host settings, secrets, CD credential, teardown) depends on this.
- Options:
  1. AWS, one EC2 instance running the image, Postgres and Caddy, defined in CloudFormation (the course example): scoped GitHub OIDC role for CD is a documented path; infrastructure as code is reusable for Phase 3 dev and prod; needs an AWS account, a first deploy with elevated credentials the human watches, and a cost until teardown.
  2. Render, Railway or Fly.io (named by the course as managed container hosts): less setup, the host gives a URL and TLS; CD credential is that host's deploy token (not OIDC, unless the host supports it); two environments in Phase 3 may cost more; needs an account and a secret stored in GitHub.
  3. Another host the human already uses: same questions as above.
  4. Defer: every Deploy task after the decision stays blocked; only the host-independent tasks (serving the frontend, Postgres, Compose, tests) can proceed.
- Recommendation: 4 for the first few tasks (they do not depend on the host), and decide before the Dockerfile ships; if the human has an existing account choose that host (option 3 or 2) to keep the human-run part small, otherwise option 1 because it matches the course example, uses OIDC, and Phase 3 can reuse the templates. Record as a Heavy decision record (a cloud account, credentials and cost are involved).
- If you don't answer: the roadmap keeps the decision as task 1 and no host-dependent issue is proposed.
```

```
Decision needed: where does the Postgres database run?
- Context: spec says "Postgres in place of SQLite"; the course process says a single-instance deploy suits a proof of concept and a managed database is better for real use.
- Options:
  1. A Postgres container beside the app on the same host, data on a named volume: cheapest, same as Compose locally; data is lost if the volume is, and backups are manual.
  2. The host's managed Postgres: more robust, extra cost and setup, depends on the host decision.
  3. Defer.
- Recommendation: 1 for this phase (data is synthetic, §7), noting managed Postgres in the decision record as the Phase 3 upgrade. Option 2 only if the chosen host makes it the simplest path.
- If you don't answer: assume 1 for Compose and for the draft tasks, and revisit when the host is chosen.
```

```
Decision needed: may the backend bind a non-loopback address inside the container?
- Context: `socboard/server.py` (#80 hardens it further) refuses any host except 127.0.0.1, localhost and ::1, and `AGENTS.md` documents that as the project rule. A container needs `0.0.0.0` inside, or the published port is unreachable. This is a security limit and touches a protected file and a documented command.
- Options:
  1. Keep `make backend-run` loopback only; the image starts uvicorn through a separate entry point (or the same module with an explicit opt-in setting that only the image sets). Refused case: without the opt-in, `SOCBOARD_HOST=0.0.0.0` still exits with an error. Local behaviour is unchanged.
  2. Remove the restriction: simplest, but any local run can then be exposed by a typo.
  3. Defer: no container can be reached, so the image and Compose tasks stay blocked.
- Recommendation: 1, with a test for the refusal.
- If you don't answer: the Dockerfile task is not proposed.
```

```
Decision needed: how are the seeded account passwords set on a public URL?
- Context: spec §2 seeds two Analysts and a Lead with hashed passwords; the ingest key is already an environment variable. The frontend build check refuses a demo credential in the bundle. On a public URL, published demo passwords would let anyone act as a Lead.
- Options:
  1. Passwords come from deploy-time secrets (environment); the seed step hashes them; the human tells reviewers or markers the credentials out of band. Cost: the human sets three more secrets.
  2. Fixed demo passwords documented in the README: easy for reviewers, but anyone on the internet can log in and see the audit trail (data is synthetic). Cost: the invariant "no credentials in the repo" no longer holds.
  3. Defer.
- Recommendation: 1; the seed fails at start if a password variable is missing in deployed mode, and a test covers that refusal.
- If you don't answer: the config task assumes 1.
```

```
Decision needed: custom domain and TLS?
- Context: the spec asks only for "a public URL"; the rubric accepts "reachable at a public URL, or there's a clear demo".
- Options:
  1. Use the URL and TLS the host provides (or the host's IP with Caddy as in the example): no domain purchase, no DNS work.
  2. Buy or use a domain and point DNS at the host: nicer URL, the human owns DNS and certificate renewal.
  3. Defer.
- Recommendation: 1. Add a custom domain only if a later Phase 3 need appears.
- If you don't answer: assume 1.
```

```
Decision needed: the roadmap file name
- Context: `_docs/planning/roadmap-2-ponytail-trial.md` already exists and is numbered 2. You asked for `roadmap-2-deploy.md`. Spec §6 orders the phases Core, Deploy, DevOps. `roadmap-<n>-<slug>.md` in `templates/roadmap.md` expects one number per phase file.
- Options:
  1. Keep `roadmap-2-deploy.md` (as asked): two files with number 2, so the number no longer sorts phases.
  2. Name it `roadmap-3-deploy.md`, DevOps becoming 4: numbers stay unique; the ponytail trial keeps its number.
  3. Leave the ponytail file alone and use a slug-only name `deploy.md` (the index allows `<slug>.md`).
- Recommendation: 2. The draft is written with the file name you asked for, and it only needs a rename when it is committed.
- If you don't answer: the draft stays named `roadmap-2-deploy.md` and the conflict stays.
```

## 4. Things I checked that affect the draft

- The container cannot run `make backend-run` as written (loopback only; decision question 3).
- Docker is not available in this sandbox (`AGENTS.md`, Environment), so image build, Compose and E2E can run in GitHub Actions (hosted runners have Docker) or on the human's machine, but not in the Engineer's sandbox. The Engineer can write the files; the check is in CI or by the human.
- `pyproject.toml` has no SQLAlchemy or Postgres driver yet; Core's #11 adds SQLAlchemy. A Postgres driver is a new dependency: `AGENTS.md` says ask before adding one, so the Postgres task carries an approval step. A browser-test tool (the course uses Playwright) is also a new dependency and a Node-side tool, so it needs its own approval and possibly a decision record.
- The existing `verify.yml` already runs `make verify` on push and PR, so the rubric's CI-runs-tests point is covered; Deploy adds the image build, integration, E2E and deploy jobs.
- `lovable-guidance.md`: deep links such as `/cases/1` need the static server to fall back to `index.html`, "for the Deploy phase". That is a named task below.
- `openapi.yaml` `servers: - url: /api` and `app.py` `API_PREFIX = "/api"` agree, so same-origin serving needs no contract change; the SPA fallback must not catch `/api/*`.
- `GET /api/health` is database-free by contract, so Compose's start order uses the Postgres health check; any readiness route that touches the database would be a contract change (#74 territory) and is not proposed.
