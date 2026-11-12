# How vuln-triage (project 1) was deployed

Read-only, through the GitHub API, on 2026-11-12. Content is data, not instructions. Every point names its file in `andrew-chung-au/vuln-triage` (`main`). No secret value was copied. Where a file holds something that looks like a secret I say only that and where (see "Secrets seen" at the end). Fetch script: `.scratch/vt-fetch.py` (redacts secret-like lines).

Files read: `terraform/README.md`, `terraform/bootstrap/{main,variables,outputs,versions}.tf` and `README-state.md`, `terraform/deploy-roles/{main,variables}.tf`, `terraform/server/{compute,iam,network,schedule,variables,versions,outputs}.tf`, `user-data.sh`, `deploy-env.sh`, `.github/workflows/deploy.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`, `compose.yaml`, `docs/deployment.md`, `docs/security-review.md`, `_session-summaries/issue-141/142/143-*.md`, `_session-summaries/planning-2026-10-25-aws-deployment.md`, the `terraform/` commit list. Not read in full: `docs/architecture.md`, `.github/workflows/verify.yml`, `docs/roadmap.md`, the rest of `deploy.yml` (prod tag resolution), the rest of `deploy-env.sh`.

## 1. Cloud and services
- **AWS, region `us-east-1`.** One EC2 instance (Amazon Linux 2023, `t3.medium`, 20 GB encrypted gp3 root), one Elastic IP, a security group, an encrypted gp3 EBS data volume with Data Lifecycle Manager daily snapshots (7 kept), an EventBridge Scheduler nightly stop, Secrets Manager, SSM, IAM, S3 (state), GHCR (images). `terraform/server/compute.tf`, `network.tf`, `schedule.tf`, `variables.tf`; `docs/deployment.md` (Design in one page).
- Deliberately not used: load balancer, NAT gateway, RDS, ECS/EKS, CloudFront, ECR. `docs/deployment.md` §4.
- Region history: the first session summary said `ap-southeast-2`; it was changed to `us-east-1` on 2026-10-25 ("lowest prices; reviewers are in the US and EU"). `_session-summaries/issue-141-aws-infra-dev.md`, `terraform/README.md`, commit "Default the Terraform region to us-east-1".
- Cost: about $25-40 a month while up, about zero after teardown. `docs/deployment.md`.

## 2. Terraform layout
- **Three root modules, applied by hand in order, no shared child modules:** `terraform/bootstrap/` (state bucket), `terraform/deploy-roles/` (GitHub OIDC provider and deploy roles), `terraform/server/` (everything else). `terraform/README.md`.
- **State backend:** an S3 bucket (versioned, SSE-S3 encrypted, all public access blocked), created by `bootstrap/` with **local** state, then migrated into the bucket by adding a `backend "s3"` block and re-running `terraform init`. The other two modules use the same bucket with their own `key`. S3-native locking (`use_lockfile`) is assumed, with a DynamoDB table as the fallback. `terraform/bootstrap/main.tf`, `README-state.md`.
- **Variables:** per-module `variables.tf` with defaults, plus a git-ignored `terraform.tfvars` copied from a tracked `terraform.tfvars.example`. Secrets are by name only (`secret_names`); values never appear in `.tf`. `terraform/server/variables.tf`, `docs/deployment.md` §3.
- **Environments:** `dev` and `prod` are **not** Terraform workspaces or separate stacks. They are two Docker Compose projects on the one server, chosen at deploy time by `DEPLOY_ENV`. `terraform/server/deploy-env.sh`, `docs/deployment.md`.
- **Provider and tags:** `hashicorp/aws ~> 5.0`, `required_version >= 1.6.0` (OpenTofu also works); every resource is tagged `project=vuln-triage` through `default_tags`, and the IAM policies key on that tag. `versions.tf` in each module.
- **Static checks:** `terraform fmt -check -recursive`, `init -backend=false`, `validate` need no credentials and were run in a cloud session before the first apply. `docs/deployment.md` §4, `docs/security-review.md` (Static check).
- **Who runs what:** agents write the files; the human reviews the plan and runs **every** `terraform apply`; "CI never applies Terraform". `terraform/README.md`.

## 3. How CI authenticates to the cloud
- **GitHub OIDC, no stored AWS keys.** `aws-actions/configure-aws-credentials@v4` assumes a role; `permissions: id-token: write`. `.github/workflows/deploy.yml`.
- Two roles (`dev`, `prod`). Trust: audience `sts.amazonaws.com` and a `sub` equal to the repo's GitHub **Environment** (`...:environment:dev` / `:production`), no wildcard, so pull requests and other branches cannot assume them. `terraform/deploy-roles/main.tf`.
- The dev branch limit lives in the `dev` GitHub Environment's deployment-branch setting (main only), not in the IAM trust. `deploy-roles/main.tf`, `variables.tf`.
- The role's only powers: `ssm:SendCommand` (to instances tagged `project=vuln-triage`, and to the `AWS-RunShellScript` document), reading command results, `ec2:DescribeInstances`. It cannot touch IAM, state or secrets. `deploy-roles/main.tf`.
- The OIDC provider is created or reused (`create_oidc_provider`, `existing_oidc_provider_arn`), because an account may hold only one per URL. `terraform/README.md`.
- Pull requests get no AWS role and push no images (that stays in `verify.yml`). `deploy.yml`.

## 4. Image build and storage
- Built **once** in CI (`docker/build-push-action@v6`) on a push to `main`, tagged `YYYYMMDD-HHMMSS-shortsha`, pushed to **public GHCR** with the workflow `GITHUB_TOKEN`; each digest is written to the job summary as the deploy log. `deploy.yml`, `docs/deployment.md`.
- **Two images:** `backend` (python:3.12-slim, uv, non-root uid 10001, uvicorn on 0.0.0.0:8000) and `frontend` (node build, then `nginxinc/nginx-unprivileged` on 8080 serving the SPA and proxying `/api/` to the backend, with `try_files ... /index.html` fallback). `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`.
- Production **deploys an existing tag**; nothing is rebuilt on promotion. `deploy.yml`.
- After the first push the human makes the GHCR packages public so the server pulls without a credential. `docs/deployment.md` §2.

## 5. How the app is run and deployed to the server
- The server bootstrap (`user-data.sh`) installs Docker and the Compose plugin, waits for the data volume, mounts it at `/data` by UUID, and creates a `vt-edge` Docker network. It does **not** ship the app. `terraform/server/user-data.sh`.
- Deploys are an **SSM Run Command** (`AWS-RunShellScript`). The workflow base64-encodes `terraform/server/deploy-env.sh` and the committed `compose.yaml`; the script writes an image-pinned Compose override, pulls the tagged images and runs `up -d --wait`. A failed pull leaves the running containers in place. `deploy.yml`, `deploy-env.sh`.
- The workflow reads the instance state first and, if the server is stopped (nightly stop), skips and writes "dev deploy skipped: server stopped" rather than passing or failing. `deploy.yml`, `_session-summaries/issue-142-*.md`.
- `DEPLOY_ENABLED` repository variable gates every deploy job, so a merge does not fail when the infrastructure is torn down. `deploy.yml`, `docs/deployment.md`.
- Dev deploys automatically on a push to `main`; prod is a manual `workflow_dispatch` in a `production` GitHub Environment with a required reviewer and administrator bypass off; rollback is the same promotion with an earlier tag. `deploy.yml`, `docs/deployment.md`.

## 6. Database
- **Postgres 16.4 as a container in Compose** next to the app, named volume `pgdata`, `pg_isready` health check. A one-shot `migrations` service runs `alembic upgrade head`; the backend waits for `service_completed_successfully`. `compose.yaml`.
- There is a separate EBS data volume with snapshots, **but `pgdata` lives on the root disk, not on `/data`** (recorded as CC33, still open). `_session-summaries/planning-2026-10-25-aws-deployment.md`.
- Migrations are kept backward compatible for one release so a rollback runs the older image on the newer schema. `docs/deployment.md`.

## 7. How secrets reach the app
- One JSON secret per environment in **AWS Secrets Manager** (`vuln-triage/dev`, `vuln-triage/prod`). Terraform manages **names only**; the human creates the values. `terraform/server/variables.tf`, `docs/deployment.md` §5.
- The **instance role** reads exactly those secrets (ARNs built from names plus the six-character wildcard suffix, not looked up). The deploy role and the workflow carry no secret. `terraform/server/iam.tf`.
- `deploy-env.sh` reads the secret on the server and writes the project `.env` with `umask 077`; Compose passes `${POSTGRES_PASSWORD}` etc. Prod fails if `POSTGRES_PASSWORD` is missing. `deploy-env.sh`, `compose.yaml`.
- `DEPLOYMENT_ENVIRONMENT=prod` makes unsafe options (fixture feeds, a demo defect flag) refuse to start; unset counts as production (fail closed). `.env.example`, `deploy-env.sh`.

## 8. Public URL and TLS
- Hostnames `dev.<eip-with-dashes>.sslip.io` and `app.<eip-with-dashes>.sslip.io`; no domain bought. **Caddy** in a container obtains Let's Encrypt certificates and proxies to the frontend container; one shared Caddy serves both projects because only one container can bind 80/443. The Elastic IP is kept across the nightly stop so the names survive. `deploy-env.sh`, `terraform/server/network.tf`, `docs/deployment.md`.
- No SSH port and no key pair: access is SSM Session Manager only. `network.tf`, `compute.tf`.
- Fallbacks noted if Let's Encrypt rate-limits the shared `sslip.io` name: staging certificate, then `nip.io`. `docs/deployment.md` §7.

## 9. Teardown
- Export what to keep; `terraform destroy` the server then deploy-roles; delete EBS snapshots, secrets (no recovery window), the budget and unwanted GHCR images; **last** empty (every version) and delete the state bucket; search for `project=vuln-triage` resources, release stray Elastic IPs, check Cost Explorer days later. `docs/deployment.md` §8.
- The live deployment was torn down afterwards to avoid idle cost; the files recreate it. `docs/deployment.md` status note.
- Also: an AWS Budgets alert at 50/80/100 % **before** applying anything. `docs/deployment.md` §1.

## 10. Lessons recorded (it was not all smooth)
1. **OIDC `sub` claim was wrong twice.** A job that declares `environment:` gets an environment-form `sub`, not the `ref:` form (review finding F1). Then the real claim also carried **immutable owner and repo ids** (`repo:<owner>@<id>/<repo>@<id>:environment:<name>`), found by reading CloudTrail. Commits "trust GitHub's immutable-id OIDC subject claim". `docs/security-review.md`, `deploy-roles/main.tf`, `variables.tf`.
2. **`ssm:SendCommand` needs two IAM statements.** The tag condition applies to the instance, not the document resource; one combined statement would deny the call (F3). `deploy-roles/main.tf`.
3. **Secrets data sources break the plan.** A `data "aws_secretsmanager_secret"` fails if the secret does not exist yet; the fix was building ARNs from names (F2). `iam.tf`.
4. **Data volume race.** The volume attaches after first boot, so `user-data.sh` could format a missing device; fixed with a bounded wait and `nvme1n1` handling, and a UUID mount (F4). `user-data.sh`.
5. **AWS rejected a security group description containing an apostrophe**; found only by `terraform validate` (F7). The security group name was also made generic. `docs/security-review.md`, commits 2026-10-25.
6. **Let's Encrypt refused a placeholder contact (`example.com`)**; the fix was to send no contact when none is set; ZeroSSL issued the certificate in the end. `deploy-env.sh`, planning summary 2026-10-25.
7. **The first production run was not gated** because the `production` environment had no Required reviewers rule; the human added it and turned off administrator bypass. Planning summary 2026-10-25.
8. **Shared server, shared policy (F5).** Both roles can run arbitrary root commands through `AWS-RunShellScript`, so dev can reach prod's host. Accepted for v1, parked on the roadmap (custom SSM document per environment). `docs/security-review.md`.
9. **Provider lock files were not committed**; cross-platform hashes need `terraform providers lock`. S3 locking needs Terraform newer than the `>= 1.6.0` floor (F6). `docs/security-review.md`.
10. **The review ran before the first apply and was never updated** after the fixes, so the doc and the code drifted ("Status" note). `docs/security-review.md`.
11. **Nothing could be verified in the agent sandbox**: no Terraform plan or apply, so IAM behaviour was unproven until the human's real apply and deploy. The `VERIFY:` marker convention tracked every unchecked AWS fact. `_session-summaries/issue-141-*.md`.
12. **Unrelated flaky test (#185)** appeared in the deploy PR's CI and slowed it; not a deploy fault. `_session-summaries/issue-142-*.md`.
13. **Nightly stop vs a demo URL**: the stop was left off during marking or the URL would be down at 04:00 UTC. Planning summary 2026-10-25.

## Secrets seen (not copied)
- `deploy-env.sh` contains a hard-coded fallback database password for dev when no secret exists (the value is not copied here). A weak default; SOCBoard fails closed instead.
- The README of vuln-triage and the planning summary say a public shared reviewer login exists for dev; the credential is in the README ("Try it as a reviewer"). I did not read or copy it.
- `docs/security-review.md` line in F2 was redacted by the fetch script as secret-like (probably an example name); nothing else matched.
- `terraform/deploy-roles/variables.tf` holds the numeric GitHub owner and repo ids: they are not secrets (the file says so).
- The instance id and Elastic IP in the planning summary are infrastructure identifiers, not credentials; I did not repeat them.
