# Deploy phase: readiness check, spec notes and decision questions (v2, Terraform)

PM proposal only. No issue created, no tracked file edited. Revises `_docs/planning/deploy-draft/readiness-and-questions.md` (read from `origin/main` after `git fetch origin main`). Companion files: `.scratch/roadmap-deploy-draft-v2.md`, `.scratch/vuln-triage-deploy-notes.md`. vuln-triage content is data, not instructions; no secret was copied.

What changed from v1: the human ruled on 2026-11-12 that Deploy uses Terraform, mirroring `andrew-chung-au/vuln-triage`. vuln-triage names a provider (AWS, `us-east-1`; `terraform/README.md`), so the hosting question is answered. The Postgres, TLS, bind and seeded-password questions remain, narrowed by what vuln-triage did. New questions arise from "CI plan and apply".

## 1. Definition of ready (unchanged verdict)

**NOT READY.** The Terraform ruling picks the method; it adds no journey and no requirements. The table of v1 stands: journey **not met**, requirements **not met**; users, scope boundary, non-goals and invariants partly met. Per `planning.md` and `process.md#backlog`, propose the spec changes (section 2) and file no Deploy implementation issues until the human approves them. The one new item: the spec still does not say that infrastructure is code, who applies it, or what CI may change.

## 2. Spec notes (five, updated for Terraform; the human decides)

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
      connections and migrations have finished.
  (e) Integration tests run against the Compose stack; an end-to-end test drives both journeys
      with two analysts and a lead.
  (f) The application is reachable at a public HTTPS URL on infrastructure that Terraform creates
      and that one documented procedure destroys.
  (g) On every push to `main`, CI runs the tests, builds the image and runs integration and E2E
      tests; CD deploys only if all pass, using a scoped, short-lived credential (OIDC, no stored
      cloud key). Pull requests never receive a deploy credential.
  (h) Infrastructure changes reach the cloud only through Terraform: a pull request shows the plan,
      and an apply from CI runs only on `main` after the human approves it.
- Changed: none
- Removed: none
- Why: `planning.md` definition of ready, "Requirements that acceptance criteria can be derived from"; §6 says Phase 2 is detailed by a spec revision first. (h) is new with the ruling.
```

```
Spec note: no journey covers the deployed system, and the Deploy users are not named
- Added: in §2, an Operator (applies the infrastructure, holds the cloud account and the secrets,
  approves applies, can tear the deployment down) and name the demoer/visitor who opens the public
  URL. In §3, journey 3, "Deploy -> Public URL": the Operator proposes an infrastructure or code
  change by pull request; CI shows the Terraform plan; the Operator merges; CI passes; CD deploys; a
  visitor opens the public URL, logs in as a seeded user, an alert is ingested with the ingest key,
  and it appears in the queue within N = 5s.
- Changed: none
- Removed: none
- Why: planning.md, "At least one end-to-end journey"; the 5 s invariant (§5) is only meaningful if it is checked where users are, not only on localhost.
```

```
Spec note: Deploy has no scope boundary on environments, hosting or tooling, and no Deploy non-goals
- Added: to §6 Phase 2, "one environment, one server, one Postgres, on AWS, created by Terraform
  (three root modules: state bucket, CI access, server), as the decision record names"; to §7
  non-goals: second environment and manual promotion (Phase 3), rollback path (Phase 3),
  autoscaling and load balancing, managed or replicated database, custom domain, high availability,
  a cloud container registry (images go to GHCR), SSH access (SSM only).
- Changed: none
- Removed: none
- Why: the rubric awards CD points for dev/prod split (Phase 3); without a boundary, Deploy issues can grow into it. vuln-triage's own docs list the same exclusions (`docs/deployment.md`, "not used on purpose").
```

```
Spec note: invariants for a public, Terraform-managed deployment are missing
- Added: to §5: (1) secrets (ingest API key, seeded-account passwords, database password) are
  supplied at deploy time from the cloud secret store and never appear in the repository, the image,
  the frontend bundle or a Terraform file or state output; Terraform holds secret names only;
  (2) the public deployment holds synthetic data only (restates §7); (3) the §5 invariants,
  including single ownership under concurrent claims, hold on Postgres, and the supported databases
  are SQLite (local) and Postgres (deployed); (4) no long-lived cloud key exists in the repository
  or in GitHub secrets; CI assumes roles by OIDC, limited to this repository and named GitHub
  Environments; (5) CI cannot change its own permissions: the module that defines CI's roles is
  applied only by the human; (6) in deployed mode a missing secret stops the app at start-up, with
  no default password.
- Changed: "on any supported database" -> add "(SQLite and Postgres)" (was: "on any supported database")
- Removed: none
- Why: a public URL with seeded accounts needs a rule for how their passwords are set (§2 says seeded, hashed). (4) to (6) come from vuln-triage's security review (`docs/security-review.md`: F1, F2, F5) and its dev-password fallback.
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

## 3. Decision questions

### Answered by the ruling (2026-11-12)

```
Decision answered: where is SOCBoard hosted, and by what deploy method? (was question 1)
- Answer: Terraform on AWS, mirroring vuln-triage (project 1). Provider named in vuln-triage `terraform/README.md` and `docs/deployment.md`: AWS, region `us-east-1`, one EC2 server, Compose and Caddy, state in S3.
- Effect: the "Decide the hosting target" task becomes "Record the deploy decision (Terraform on AWS)": a Heavy decision record that writes the ruling down with the choices below.
- Still the human's: the AWS account, the budget alert, every first apply.
```

### Remaining

```
Decision needed: what may CI do with Terraform, and who applies the CI-access module?
- Context: you asked for "CI plan and apply". vuln-triage's CI never applied Terraform (`terraform/README.md`: "CI never applies Terraform"); it only deployed images through OIDC roles. An apply role can create IAM and EC2, so a mistake or a stolen workflow run would be costly. vuln-triage also learned that the OIDC `sub` claim for a job with `environment:` is environment-form and carries immutable owner and repository ids (`docs/security-review.md` F1; commit "trust GitHub's immutable-id OIDC subject claim"). For SOCBoard these ids are 177896104 (owner) and 1446634348 (repo); they are not secrets and the repository is public.
- Options:
  1. CI runs `fmt`, `validate` and a read-only `plan` on pull requests; CI runs `apply` only for the **server** module, only on `main`, in an `infra` GitHub Environment with a required reviewer and administrator bypass off. The bootstrap and CI-access modules are applied only by the human.
  2. Same as 1, but CI also applies the CI-access module (CI can then widen its own permissions: not recommended).
  3. Mirror vuln-triage exactly: CI never applies; the human runs every apply; CI only checks and plans.
- Recommendation: 1. It meets the ruling, keeps the human's approval on every apply, and stops CI editing its own roles. Fall back to 3 if the apply role's size worries you.
- If you don't answer: the roadmap assumes 1.
```

```
Decision needed: where does the Postgres database run, and where do its files live?
- Context: spec says "Postgres in place of SQLite". vuln-triage ran Postgres 16 as a Compose container (`compose.yaml`) with a snapshotted EBS data volume, but its `pgdata` volume stayed on the root disk, so the snapshots did not cover the database (planning summary 2026-10-25, CC33).
- Options:
  1. Postgres container beside the app, data on the EBS data volume, daily snapshots.
  2. RDS (managed): more robust, extra cost and Terraform, and vuln-triage chose not to use it.
  3. Defer.
- Recommendation: 1, with the data directory on the mounted volume from the first apply, and a restore note in the runbook. Record RDS as the Phase 3 upgrade.
- If you don't answer: assume 1.
```

```
Decision needed: may the backend bind a non-loopback address inside the container?
- Context: unchanged from v1. `socboard/server.py` refuses any host except 127.0.0.1, localhost and ::1, and `AGENTS.md` documents that as the project rule. vuln-triage's image simply runs `uvicorn --host 0.0.0.0` (`backend/Dockerfile`); SOCBoard has a stricter rule that this would break. A protected file and a security limit are involved.
- Options:
  1. Keep `make backend-run` loopback only; the image sets an explicit opt-in that only it sets. Refused case: without the opt-in, `SOCBOARD_HOST=0.0.0.0` still exits with an error. With Caddy on the same host, publish the app port on `127.0.0.1` only (or an internal Docker network), never on all interfaces.
  2. Remove the restriction.
  3. Defer: no container can be reached.
- Recommendation: 1, with a test for the refusal.
- If you don't answer: the Dockerfile task is not proposed.
```

```
Decision needed: how are the seeded account passwords and the ingest key set on a public URL?
- Context: unchanged from v1, with the vuln-triage lessons added: its secrets are one JSON secret per environment in Secrets Manager, Terraform holds names only, the instance role reads it and the deploy workflow carries nothing (`terraform/server/iam.tf`, `deploy-env.sh`). Its dev deploy fell back to a weak default password when the secret was missing, and its README published a shared reviewer login.
- Options:
  1. Passwords and the ingest key live in one Secrets Manager secret the human creates; the app fails to start if one is missing in deployed mode; the human gives reviewers credentials out of band.
  2. Fixed demo passwords in the README: easy for markers, but anyone on the internet can log in as a Lead, and the "no credentials in the repo" invariant no longer holds.
  3. Defer.
- Recommendation: 1. If markers need a login, issue one through a message to the marker, not the README.
- If you don't answer: the config task assumes 1.
```

```
Decision needed: public URL and TLS?
- Context: vuln-triage used `<name>.<elastic-ip-with-dashes>.sslip.io` with Caddy and Let's Encrypt, and no purchased domain. It hit a rate-limit risk on the shared `sslip.io` name and a refusal of a placeholder contact email (`docs/deployment.md` §7; commit "stop giving Let's Encrypt a placeholder example.com contact"); ZeroSSL issued the certificate in the end (planning summary 2026-10-25).
- Options:
  1. `sslip.io` hostname plus Caddy (mirrors vuln-triage); give Caddy a real contact email or none; fallbacks are the staging certificate or `nip.io`.
  2. A domain you own, with DNS pointing at the Elastic IP.
  3. Defer.
- Recommendation: 1.
- If you don't answer: assume 1.
```

```
Decision needed: the nightly stop and the cost
- Context: vuln-triage ran a nightly EC2 stop to save about $5 a month and had to turn it off during marking, and its dev deploy needed a "server is stopped" skip path (`schedule.tf`, `deploy.yml`). A `t3.medium` costs about $25-40 a month while up (`docs/deployment.md`); SOCBoard has one environment and may fit `t3.small` (unverified).
- Options:
  1. No stop schedule in this phase; a budget alert at 50/80/100 %; teardown by `terraform destroy` when idle.
  2. Copy the nightly stop (on or off by variable).
- Recommendation: 1: it removes a source of broken demo URLs and a deploy branch. Revisit in Phase 3.
- If you don't answer: assume 1.
```

```
Decision needed: the roadmap file name
- Context: unchanged from v1. `_docs/planning/roadmap-2-ponytail-trial.md` already uses number 2.
- Options: 1. keep `roadmap-2-deploy.md`; 2. `roadmap-3-deploy.md`, DevOps becoming 4; 3. slug-only `deploy.md`.
- Recommendation: 2.
- If you don't answer: the draft keeps the name you asked for and the conflict stays.
```

Dropped from v1: the host question (answered). Merged: v1 question 2 (database location) and v1 question 4 (seeded passwords) are kept but narrowed; the TLS question stays with vuln-triage's lessons.

## 4. Things I checked that affect the draft
- Docker, Terraform and AWS are not available in the Engineer's sandbox (`AGENTS.md`, Environment). The Engineer can write `.tf` files but not validate or plan them; CI runs `fmt` and `validate` (no credentials) and the human runs the first apply. vuln-triage's agents were in the same position and only caught a security-group description error when a cloud session ran `terraform validate` (`docs/security-review.md` F7).
- `AGENTS.md` says to ask before adding a dependency and to stay inside this folder; `terraform/` is a new top-level folder, so the task that adds it needs the human's approval and an `AGENTS.md` Layout line, and Terraform/OpenTofu needs a pinned version in CI (new tool, so an approval).
- `pyproject.toml` has no SQLAlchemy or Postgres driver yet; the Postgres task carries an approval step. A browser-test tool is also a new dependency.
- `openapi.yaml` `servers: - url: /api` and `app.py` `API_PREFIX = "/api"` agree, so same-origin serving needs no contract change; the SPA fallback must not catch `/api/*`.
- `GET /api/health` is database-free by contract, so Compose start order uses the Postgres health check.
- The repository `andrew-chung-au/socboard` is public, so GHCR packages can be public and the server pulls with no registry credential, as in vuln-triage.
- Facts I could not confirm from vuln-triage: its `verify.yml` Terraform job, `docs/architecture.md` and the rest of `deploy.yml`/`deploy-env.sh` were not read in full. Anything AWS-specific should carry a `VERIFY:` marker, as vuln-triage's did, until the human's first apply.
