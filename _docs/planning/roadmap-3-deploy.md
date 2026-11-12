# Roadmap 3: Deploy

Order and status only. Acceptance criteria live on the issues; open/closed is read from the tracker. Scope and reasons: `_docs/specs.md` (§3 journey 3, §4 Deploy stories, §5 invariants, §6 Phase 2). Method: `_docs/planning/03_deployment.md` (course material for this project, followed here). Roadmap 1 (Core) is `roadmap-1-core.md`; DevOps becomes roadmap 4.

- **Status:** next (Core is now). It starts when Core ends and when the Deploy spec changes (human-approved 2026-11-12) are committed to `_docs/specs.md`. Tasks are proposed issues without numbers; each is filed after the human approves the backlog proposal and gets its number then.
- **Goal:** SOCBoard runs from one container image with Postgres, starts with one Docker Compose command, is covered by integration and end-to-end tests, is reachable at a public HTTPS URL on AWS created by Terraform, and is deployed by CI/CD only when the tests pass.
- **Who:** **CI** = GitHub Actions can run it; **Human** = needs the human (AWS account, `terraform apply`, GitHub settings, secrets); **Eng** = the Engineer in the sandbox (no Docker, Terraform, AWS credentials or push).

## Decisions (human, 2026-11-12; recorded by task 1)

- **Terraform on AWS**, one environment, one server. Infrastructure as code makes the environment repeatable.
- **CI and Terraform.** Pull requests run `fmt`, `validate` and a read-only `plan`. `apply` runs from CI only for the server part, only on `main`, in an `infra` GitHub Environment where a named reviewer must approve and administrators are not exempt. The state bucket and the CI-access part are applied only by the human, so CI cannot widen its own permissions.
- **Bind address.** `make backend-run` stays loopback-only. The image sets an explicit opt-in; without it `SOCBOARD_HOST=0.0.0.0` still exits with an error, and a test covers the refusal. The app port is published on the host's loopback only, behind Caddy.
- **Postgres** runs as a container beside the app, with its files on the encrypted data volume and daily snapshots. A restore note goes in the runbook. A managed database (RDS) is the Phase 3 upgrade.
- **Secrets.** The ingest key, the seeded passwords and the database password live in one Secrets Manager secret the human creates. In deployed mode the app refuses to start if one is missing. The human hands reviewers their credentials out of band, not in the README.
- **URL and TLS.** A hostname derived from the server's Elastic IP through a public wildcard-DNS service, with Caddy issuing the certificate automatically (a real contact address or none). While testing, use the certificate authority's staging endpoint or another wildcard-DNS service to stay under rate limits.
- **Cost.** No stop schedule in this phase. Budget notifications at half, 80 percent and the full limit; `terraform destroy` when idle. Revisit in Phase 3.
- **Roadmap name:** `roadmap-3-deploy.md`, because number 2 is the ponytail trial.

## SOCBoard's Deploy design, and why

- **One small server running Docker Compose.** SOCBoard is one image plus Postgres; one server keeps cost and moving parts low for a single environment.
- **One image.** FastAPI serves the built SPA, so there is no second web server container. Caddy in front only terminates TLS and proxies to the app.
- **Remote state in a private, versioned, encrypted S3 bucket.** State holds resource details and must survive the laptop. The bucket is created first with local state, then state is moved into it.
- **Keyless CI through GitHub OIDC.** Separate roles for plan (read-only), apply and deploy, each trusted only for this repository and a named GitHub Environment. Trust uses the repository's numeric ids so a renamed or recreated repository cannot reuse a role.
- **Access through AWS Systems Manager only.** No SSH key and no port 22; the security group opens 80 and 443. CI deploys by sending a command through Systems Manager.
- **Images in the public GitHub Container Registry**, pushed with the workflow's own token. The repository is public, so the host needs no registry login.
- **Persistent data on its own encrypted EBS volume with scheduled snapshots**, holding the Postgres files.
- **Requirements that follow from SOCBoard itself:**
  - In deployed mode the app refuses to start if any secret is missing; there is no default password.
  - `/api/health` stays database-free (`openapi.yaml`), so start order uses the Postgres health check.
  - Postgres must keep exactly one owner under simultaneous claims (spec §5).
  - The public deployment holds synthetic data only (spec §5, §7).
  - Every cloud resource carries the tag `project=socboard`, and IAM conditions use it.
  - Nothing is proven until a real apply. Eng cannot run `plan` or `apply`, so CI runs `fmt` and `validate` and the human runs the first apply. Mark AWS-specific details unverified (`VERIFY:`) in the Terraform issues.

## Tasks, in order (proposed issues, one deliverable each)

1. **Record the deploy decision (Terraform on AWS).** Heavy decision record holding the seven decisions above with their reasons, plus region and accepted risks. Who: Human approves; PM drafts (`.scratch/deploy-decisions.md`).
2. **Serve the built frontend from FastAPI.** Screen routes return the SPA entry page; unknown `/api/*` still returns the JSON 404 `not_found`. Needs: #10, #21, #27. Who: Eng; CI.
3. **Read configuration from the environment.** Database URL, ingest key, seeded passwords and poll interval from env vars, with an example env file; a missing secret stops start-up in deployed mode. Needs: #11, #13, #14. Who: Eng; CI.
4. **Run on Postgres.** Backend and migrations work on Postgres chosen by the database URL; SQLite stays the local default. Adds a driver dependency (approval first). Needs: #11. Who: Eng; CI with a Postgres service container.
5. **Prove single ownership on Postgres.** Two simultaneous claims leave exactly one owner. Needs: #16, task 4. Who: Eng; CI.
6. **Let the container bind a non-loopback address safely.** Explicit opt-in set only by the image; the refusal for `make backend-run` stays and is tested. Needs: #10, #80. Who: Eng; Human approves the protected-file change.
7. **Build one container image.** Multi-stage `Dockerfile`: build the frontend, copy `frontend/dist` into a non-root Python image. Needs: #21, #27, tasks 2, 3, 6. Who: Eng writes; CI builds; Human checks once locally.
8. **Add the Compose stack.** `docker-compose.yaml` with the app, a one-shot migration step and Postgres (health check, named volume), plus one `make` target. Needs: #22, tasks 4, 7. Who: Eng; CI or Human runs.
9. **Integration tests against the Compose stack.** Frontend served, backend reaches Postgres, login, ingest and queue work. Needs: #14, #15, #22, task 8. Who: Eng; CI.
10. **End-to-end test of both journeys.** Browser test with two analysts and a Lead: claim, refused second claim, close with a reason, audit-trail read. New browser-test tool (approval). Needs: #17, #19, #21, #22, task 8. Who: Eng; CI.
11. **CI builds the image and runs integration and E2E tests.** Unit tests in parallel, then image build, then integration and E2E, beside `make verify`. Needs: tasks 9, 10. Who: Eng; CI.
12. **Terraform: state bucket.** Private, versioned, encrypted S3 bucket, applied once then state migrated; `.gitignore` for state and real variable files, example variable file tracked. A new top-level `terraform/` folder needs the human's approval and a Layout line in `AGENTS.md`. Needs: task 1. Who: Eng writes; Human applies.
13. **Terraform: CI access.** OIDC provider, plan role, apply role (Environment `infra`), deploy role (Environment `deploy`), trust on repository ids, narrowly scoped Systems Manager send-command permission, no stored keys. Human-applied only. Needs: tasks 1, 12. Who: Eng writes; Human applies and creates the GitHub Environments.
14. **Terraform: server.** EC2 instance with no key pair, Elastic IP, security group (80, 443), instance role (Systems Manager plus read of the one named secret), encrypted data volume with daily snapshots holding Postgres files, bootstrap installing Docker, Compose and Caddy, budget notifications at three thresholds, `project=socboard` tag. Needs: tasks 1, 12. Who: Eng writes; Human applies.
15. **CI: Terraform checks and plan.** On pull requests touching `terraform/`: `fmt -check`, `init -backend=false`, `validate` without credentials, then a read-only `plan` shown on the PR. No role for forks. A pinned Terraform version in CI is a new tool (approval). Needs: tasks 12, 13, 14. Who: Eng; CI.
16. **First apply by the human.** Checklist: AWS account and budget notifications, GitHub Environments (`infra`: reviewer approval needed, administrators not exempt), apply the three parts in order, create the secret, note the IP and hostname, re-run `plan` and expect no changes. Who: **Human**; Eng drafts.
17. **Deploy script and first deploy.** Server-side script run through Systems Manager: pull the image by tag, read the secret with the instance role, write the env file with restrictive permissions, run Compose, reload Caddy; if the image cannot be fetched, the current release keeps serving. The human runs the first deploy and the HTTPS URL loads. Needs: #22, tasks 1, 7, 8, 16. Who: **Human** first run; Eng writes.
18. **Post-deploy smoke check.** A `make` target that, given a URL, checks `/api/health`, login, one ingest, and that the alert reaches the queue within 5 s. Needs: #13, #14, #15. Who: Eng; CI and Human run it.
19. **Deploy from CI on a green push to `main`.** Deploy only after all tests pass, using the OIDC deploy role; record image tag and digest in the job summary; run the smoke check; Terraform apply from CI only for the server part, on `main`, after the named reviewer approves the plan. Needs: tasks 11, 15, 17, 18. Who: Eng writes; **Human** sets variables and approves; CI runs.
20. **Write the deploy and teardown runbook.** How to deploy, where the URL is, how reviewers get credentials, how to restore Postgres from a snapshot, the requirements above, and ordered deletion of every cloud resource (server, then CI access, then snapshots and secrets, state bucket last). Needs: tasks 16, 19. Who: Eng; Human performs the teardown when wanted.

- **Needs the human:** 1, 6, 7 (local Docker check), 12 to 14 (apply), 16, 17 (first run), 19 (variables, approval), 20 (teardown). **CI can run:** 2 to 5, 7 to 11, the checks and plan in 15, 18, and the pipeline in 19.
- **Not in this phase:** separate dev and production environments, promotion and rollback (DevOps, `_docs/planning/04_devops.md`); metrics, tracing, alerting; managed or replicated database, autoscaling, load balancing, high availability; custom domain; scheduled server stop; SSH access; synthetic alert generator and Lead reassignment (Should-haves); Target Enhancements. Login attempt limiting and ingest rate limiting appear in the spec's Phase 2 outline (§6) but have no Deploy story yet; no issue for them is scheduled until the human approves a story.
- **Archive when:** the public URL serves the app, the Compose `make` target starts the full system, `terraform plan` on `main` shows no changes after the human's apply, and a passing push to `main` deploys without manual steps.
