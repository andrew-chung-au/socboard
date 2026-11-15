# Roadmap 3: Deploy

Order and status only. Acceptance criteria live on the issues; open/closed is read from the tracker. Scope and reasons: `_docs/specs.md` (§3 journey 3, §4 Deploy stories, §5 invariants, §6 Phase 2). Method: `_docs/planning/03_deployment.md` (course material for this project, followed here). Roadmap 1 (Core) is `roadmap-1-core.md`; DevOps becomes roadmap 4.

- **Status:** in progress (Core ended; see the status block under Tasks). Tasks without an issue number are not filed yet; each is filed after the human approves it and gets its number then.
- **Revision, 2026-11-13 (human-approved recommendations):** the task list below is superseded in detail by `_docs/planning/deploy-backlog-proposal.md` (D1 to D23). Changes: task 4 is split (Postgres support, then the append-only audit rule on Postgres); login and ingest limiting are added; task 3 gains the token secret and treats the poll interval as a build argument; #100 is a prerequisite of the image; the image build joins CI with the Dockerfile. Region: deployment `us-east-1` (the human's own account, home region `ap-southeast-2`). Wave 1 is filed: #139 (decision record), #140 (serve the build), #141 (deployed-mode settings), #142 (container bind opt-in), #143 (Postgres). Later items are filed as wave 1 progresses.
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

## Tasks, in order (D-labels; the issue text is in `_docs/planning/deploy-backlog-proposal.md` and the draft files beside it)

Order and status only. An item with an issue number is filed; open or closed is read from the tracker. The status block below is a snapshot of 2026-11-15 and goes stale; the tracker wins. No item starts before the human approves its issue.

**Status at 2026-11-15 (from `origin/main` and the tracker)**
- **Merged and closed:** D0 (PR #179), D1 (#139), D2 (#140), D3 (#141), D4 (#142), D5a (#143), D5b (#192), D6a (#193), D6b (#195), D7 (#194), D8 (#178), D9 (#183), D10 (#190), D11 (#221), D15 (#154), D16 (#156), D17a (#162), D17b (#199), D18a (#155), D21 (#191), D24 (#185), #22, #100, #104, #107, #131, #144, #175, #212.
- **Merged, issue open:** D12 (#229, close-out pending). Its Makefile target, `AGENTS.md` line and CI step are protected-file diffs in `_human-tasks/229-proposed/` that the human has not applied; `make test-stack` is not on `main` until they are.
- **In review:** #31 (PR #232).
- **Approved, not started:** #207 (D19a; the Engineer's checklist is finished locally on `claude/207-first-apply-checklist` but not pushed), #209 (D18b), #93, #65, #49.
- **Filed and groomed, waiting for the human's label (2026-11-15):** #233 (D17c), #234 (D13), #235 (D14). Also groomed and unlabelled: #214, #215, #218.
- **Filed as outlines, not groomed (do not start):** #236 (D19b), #237 (D20), #238 (D22a), #239 (D22b), #240 (D23).
- **Open follow-ups, not on the path:** #208 (docs), #203, #205 (decide when D20 is groomed: if the deploy cannot migrate through Compose's `migrate` service, build it), #226, #160, #164, #153, #122, #112, #110, #103.

**Lanes (who takes what)**
- **Terraform lane** (the human applies; an agent never runs `apply`): push #207's checklist and, after the human's run (stage b), #209 (D18b); then D17c (#233); then D19b (#236, a human step). Later D22b (#239).
- **Compose and CI lane** (owns `compose.yaml`, the workflows, `e2e/`): D13 (#234), then D14 (#235), then the human's apply of the #229 diffs if they are still pending; later D22a (#238).
- **Application and tests lane** (Postgres, frontend, audit guard): #31, #93, #214, #215 once labelled. D20's script (#237) when groomed. No Postgres feature work is left.
- **Human steps:** label #233, #234, #235 (and #214, #215); apply the `_human-tasks/229-proposed/` diffs; run #207 stage b; set the registry package public after D14's first push; D19b; D20's first run; D22a and D22b variables and Environment approval; D23's teardown.

**Order to the first deploy:** `#207 (human run) -> #209` and `D17c (#233) -> D19b (#236)` run beside `D13 (#234) -> D14 (#235)`; all of D19b, D14 and D21 (done) then feed `D20 (#237) -> D22a (#238) -> D22b (#239) -> D23 (#240)`.

- **Needs the human:** D0 (done), D1 (done), D4, D10, D11 and D21 (protected files, done), D12, D13, D14 (protected-file diffs), D15 to D17c (apply), D19a, D19b, D20 (first run), D22a and D22b (variables, Environment approval), D23 (teardown).
- **Draft bodies:** wave 1 in `deploy-draft/wave-1-issue-drafts.md` (filed); wave 2 in `deploy-draft/wave-2-issue-drafts.md` (D6a, D6b, D8, D9, D10, D15, D16, D17a to D17c, D18a, D18b, D21 and D24 are filed; D17c as #233). D13, D14 and D17c were written at filing from the code, not from the drafts. D19b, D20, D22a, D22b and D23 have outline bodies on their issues.
- **Spec notes pending the human:** A (token secret in the secrets invariant) and B (poll interval is set at build): `deploy-draft/spec-notes-a-b.patch`.

- **Not in this phase:** separate dev and production environments, promotion and rollback (DevOps, `_docs/planning/04_devops.md`); metrics, tracing, alerting; managed or replicated database, autoscaling, load balancing, high availability; custom domain; scheduled server stop; SSH access; synthetic alert generator and Lead reassignment (Should-haves); Target Enhancements. Login attempt limiting and ingest rate limiting are in this phase (D8 and D9; spec §4 Deploy stories).
- **Archive when:** the public URL serves the app, the Compose `make` target starts the full system, `terraform plan` on `main` shows no changes after the human's apply, and a passing push to `main` deploys without manual steps.
