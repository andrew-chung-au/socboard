# Terraform: the state bucket

`state/` defines one private S3 bucket in `us-east-1` that later holds Terraform state. The human applies it once. An agent never runs `apply`, `destroy` or any step below.

**Status: not yet run.** Terraform and AWS were not available when this was written, so `fmt` and `validate` have run only in CI (the `terraform check` workflow, #155). Every "Expected" line below is what the author expects, not something seen. Every argument name in `state/main.tf` carries a `VERIFY:` comment until the first apply (Deploy item D19).

## Terraform version

`terraform/.terraform-version` holds the one version: `1.17.2`. `VERIFY:` how it was chosen: on 2026-11-13 the newest non-prerelease tag in the `hashicorp/terraform` GitHub releases list was v1.17.2 (published 2026-11-11), and native S3 locking (`use_lockfile`) has been generally available since 1.11 (from web sources, not from running it). `state/main.tf` repeats only the lower bound `>= 1.11.0`.

## Steps for the human (not yet run)

Run each step in `terraform/state/` with AWS credentials for your own account in your shell (never in a file in this repository).

1. Install Terraform at the version in `terraform/.terraform-version`.
   Expected: `terraform version` prints `Terraform v1.17.2`.
2. Copy `terraform.tfvars.example` to `terraform.tfvars` (git-ignored) and set `bucket_name` to a globally unique name of your choice. Leave your AWS account number out of it.
   Expected: `git status` does not list `terraform.tfvars`.
3. Create the bucket with local state: `terraform init`, then `terraform plan`, then `terraform apply`.
   Expected: `init` ends with "Terraform has been successfully initialized!" and writes `.terraform.lock.hcl`; `plan` shows 6 to add (bucket, public access block, versioning, encryption, lifecycle, policy); `apply` ends with "Apply complete! Resources: 6 added, 0 changed, 0 destroyed." A local `terraform.tfstate` appears (git-ignored).
4. Before committing `.terraform.lock.hcl`, add the checksums for every platform that will use it: `terraform providers lock -platform=linux_amd64 -platform=<your own platform>`. CI runs on `linux_amd64`; common choices for your own are `darwin_arm64` (Apple silicon Mac), `darwin_amd64` (Intel Mac), `windows_amd64` and `linux_arm64`. Skip the second `-platform` if your own is `linux_amd64`. Then commit `.terraform.lock.hcl`. It cannot be made without `init`, so it is not in the repository yet and is tracked once it exists.
   Expected: the command prints "Success! Terraform has updated the lock file." and `git status` lists `terraform/state/.terraform.lock.hcl` as new.
5. Copy `backend.tf.example` to `backend.tf`, then move the state: `terraform init -migrate-state -backend-config="bucket=<your bucket_name>"` and answer `yes`. Commit `backend.tf`: it holds no bucket name and no secret. The bucket name is passed to every `init` with `-backend-config`; a later CI job needs it as a repository variable (not a secret).
   Expected: `init` asks whether to copy the existing local state to the new backend; after `yes` it prints "Successfully configured the backend "s3"!" and `terraform plan` reports "No changes."
6. Check the move: `terraform state list` lists the six resources, and the S3 console shows `state/terraform.tfstate` in the bucket. Then delete the local `terraform.tfstate` and `terraform.tfstate.backup`.
   Expected: the list shows `aws_s3_bucket.state` and five more; during `plan` a `state/terraform.tfstate.tflock` object appears briefly (native lock file).

Later `init` runs need the same `-backend-config="bucket=<name>"` argument.

## Teardown (the human's step, destructive)

The bucket has `force_destroy = false` and holds every version of the state, so Terraform will not delete it while it has objects. The bucket is the last resource to go (Deploy item D23): destroy everything else first, because it holds their state. Emptying the bucket while the state still lives in it would delete the state `terraform destroy` needs, so the state moves back to a local file first. These steps, in this order, are the only supported teardown. Run them in `terraform/state/`. All are **not yet run**; the expected output is what the author expects, not something seen.

1. Delete `backend.tf` (and commit the deletion if you committed the file).
   Expected: `terraform/state/backend.tf` is gone; `backend.tf.example` stays.
2. Move the state back to local: `terraform init -migrate-state`, answering `yes`.
   Expected: `init` says it detected the backend was removed and asks to copy the state to a local file; after `yes` it prints "Terraform has been successfully initialized!" and a `terraform.tfstate` appears in `terraform/state/`. Check that `terraform state list` shows the six resources before going on.
3. The human empties the bucket of **all object versions and delete markers** (a plain delete of the current objects leaves the old versions). Destructive; an agent never does this.
   Expected: the S3 console "Show versions" view lists no objects, and the bucket is empty.
4. `terraform destroy`.
   Expected: the plan shows 6 to destroy and, after `yes`, it ends with "Destroy complete! Resources: 6 destroyed."

## What is deliberately not here

No outputs, no secrets, no KMS key. The bucket name is a variable so that no AWS account number enters the repository. Noncurrent versions expire after 90 days (`state/main.tf`).

## CI access (the GitHub OIDC provider and three roles)

`ci-access/` defines what a GitHub Actions job may do in AWS: one OIDC identity provider and three roles. **The human applies it, and only the human**: an agent never runs `init`, `plan`, `apply` or `destroy`, and CI never applies it (Deploy decision 10), so CI cannot widen its own permissions. Apply it after `state/`. It does not depend on `iam-and-budgets/` being applied first, because the apply role names the instance role by its ARN only.

**Status: not yet run.** Terraform and AWS were not available when this was written, so `fmt` and `validate` have run only in CI. Every "Expected" line below is what the author expects, not something seen. Every AWS argument and behaviour in `ci-access/*.tf` carries a `VERIFY:` comment until the first apply (Deploy item D19). Decision record: `_docs/decisions/2026-11-14T0928Z-ci-oidc-roles.md` (approved).

### What it creates

| Role | Assumed by | May do | Never |
|---|---|---|---|
| `socboard-ci-plan` | any pull request run in this repository (subject ends `:pull_request`) | read the server state under `server/` in the state bucket, `ec2:Describe*`, read the deploy document | write anything, any `iam:*`, `dlm:*` or `budgets:*` action, read a secret value |
| `socboard-ci-apply` | a job in the `infra` Environment (subject ends `:environment:infra`) | read and write the server state under `server/`, create EC2 resources in `us-east-1` only with the tag `project=socboard`, change only resources that carry it, manage the one deploy document, pass the instance role to EC2 | any IAM action but that one PassRole (explicit deny on `iam:*`, so it cannot change its own role or policy, or pass any other role), any `dlm:*` or `budgets:*` action, tag an existing untagged resource, read a secret value |
| `socboard-ci-deploy` | a job in the `deploy` Environment (dev) or the `deploy-prod` Environment (prod); the subject ends `:environment:deploy` or `:environment:deploy-prod`, nothing else | send the one deploy document to instances tagged `project=socboard`, read a command's result by its id | run any AWS-owned document (so no generic shell), any `iam:*`, `dlm:*` or `budgets:*` action, read a secret value |

- The provider is made here unless `reuse_oidc_provider_arn` is set. AWS allows one provider per URL in an account, so an account that already has one must reuse it.
- Every trust condition requires the audience `sts.amazonaws.com` and exact subjects: one for the plan and apply roles, and two for the deploy role (`deploy` and `deploy-prod`, issue #268). The subject starts with `repo:<owner>@<owner id>/<repo>@<repo id>`, which is the form GitHub reported for this repository on 2026-11-14 (its API said `use_immutable_subject: true`). The numeric ids are therefore part of every trust, and a repository that was renamed or recreated cannot reuse a role. `VERIFY:` that a real token has this shape (D19).
- **The branch limit is not in AWS.** For a job that names an Environment, the subject carries the Environment name, not the branch. A workflow on any branch that names `infra`, `deploy` or `deploy-prod` could assume the role unless the Environment lets only `main` deploy. That is the human's setting in GitHub (D19), and the check in `_human-tasks/2026-11-14T0928Z-human-task-156-engineer-ci-access-apply-and-branch-check.md` is the evidence. The Engineer has not checked it.
- **The plan role is open to every pull request**, including a pull request that edits a workflow. That is why it is read-only and cannot read a secret value. Pull requests from forks get no OIDC token from GitHub by default (`VERIFY:` in D18b).
- The state bucket is shared by every part. The plan and apply roles reach only the `server/` prefix, so the CI-applied server part (D17b) must set its backend `key` under `server/`. The state of this part and of `iam-and-budgets/` lives outside that prefix, so the CI roles can neither read nor change it. A read-only plan cannot write the native lock file, so the plan job runs with `-lock=false` (D18b).
- The apply role's EC2 list is a first guess at D17b's needs. If the first CI apply is refused an action, the human adds that one action and applies this part by hand. That is the intended cost of keeping IAM out of CI's reach.
- **CI makes no IAM change.** The only IAM action any CI role may take is the apply role passing `socboard-instance` to `ec2.amazonaws.com`. Two explicit denies do it: `iam:*` on every resource except the instance role, and every action except `iam:PassRole` on the instance role. (A plain `Deny iam:*` would also block that PassRole, since an explicit deny always wins.) IAM reads are denied too, so D17b must name the instance profile directly and use no IAM data source. The plan and deploy roles deny `iam:*` outright. All three deny `dlm:*` and `budgets:*`: the snapshot schedule and the budgets are human-applied (#162). That PassRole is the single exception to the next section's rule that CI's deny covers `iam:*` including `socboard-instance`. #156 requires it so CI can attach the instance role. `socboard-snapshot` and every other IAM resource stay fully denied. The apply role also denies `ec2:DeleteSnapshot`, `ec2:ModifySnapshotAttribute` and `ec2:DisableSnapshotBlockPublicAccess`, so it cannot delete a backup or share one outside the account.
- The apply role's EC2 writes are tag-scoped. D17b must put `project=socboard` on the instance, the data volume, the security group and the Elastic IP in the create call, and leave the root volume untagged (the next section's rule for D17b). The root volume a launch creates is therefore not tag-scoped; tags can be added afterwards only to a resource that already has it. AWS cannot tag-scope the reads (`ec2:Describe*`), the image, subnet and network interface a launch refers to, the VPC a security group is made in, a new security group rule, or the network interface named when an Elastic IP is moved; each of those calls also names a tagged resource. The role can still change the tagged instance itself, including its user data, which is how D17b manages the server.
- The deploy role reads results with `ssm:GetCommandInvocation` only. AWS authorises it on `*` with no condition key, so it cannot be limited to the deploy document or the instance; the role needs a command id and cannot list other commands.
- The deploy document is created by the deploy-script issue (D20), named `socboard-deploy`. This part only names it. The pattern that checks the image digest lives inside the document, not in this role's policy.
- No access key, user or secret exists here. No `data` source reads a secret, and no output holds a secret value. The three outputs are role ARNs.

### Outputs

`plan_role_arn`, `apply_role_arn` and `deploy_role_arn`. The workflows (D18b, D22a, D22b) take them as repository secrets (`AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN`, `AWS_DEPLOY_ROLE_ARN`), because each ARN contains the account number and Actions logs mask secrets but not variables.

### Steps for the human (not yet run)

Run each step in `terraform/ci-access/` with AWS credentials for your own account in your shell (never in a file in this repository). The state bucket from `state/` must already exist.

1. Find out whether the account already has a GitHub OIDC provider: `aws iam list-open-id-connect-providers`.
   Expected: a list of provider ARNs. If one ends in `oidc-provider/token.actions.githubusercontent.com`, put that ARN in `reuse_oidc_provider_arn` in step 2. If none does, leave the variable out. A second provider for the same URL cannot be created.
2. Copy `terraform.tfvars.example` to `terraform.tfvars` (git-ignored) and set `repo_owner_id` and `repo_id` (`gh api repos/<owner>/<repo> --jq '.owner.id, .id'`), your state bucket name, and `reuse_oidc_provider_arn` if step 1 found a provider.
   Expected: `git status` does not list `terraform.tfvars`.
3. Copy `backend.tf.example` to `backend.tf`, then run `terraform init -backend-config="bucket=<your state bucket_name>"`. Commit `backend.tf`: it holds no bucket name and no secret.
   Expected: "Successfully configured the backend "s3"!" and "Terraform has been successfully initialized!"; `.terraform.lock.hcl` appears.
4. Before committing `.terraform.lock.hcl`, add the checksums for every platform that will use it: `terraform providers lock -platform=linux_amd64 -platform=<your own platform>` (skip the second if yours is `linux_amd64`). Then commit it.
   Expected: "Success! Terraform has updated the lock file."
5. `terraform plan`, and read it. Check that each role's trust names the audience and its exact subjects (one each for the plan and apply roles, two for the deploy role), that the apply role's policy has the deny statements, and that the deploy role's policy names no AWS-owned document.
   Expected: "Plan: 7 to add, 0 to change, 0 to destroy." (the provider, three roles and three inline role policies; 6 if you reuse the account's provider). Stop on any surprise.
6. `terraform apply`, answering `yes`.
   Expected: "Apply complete! Resources: 7 added, 0 changed, 0 destroyed." (6 if you reuse the provider) and three role ARNs printed.
7. Do the checks in the human task file named above (the Environments' branch limit and the refused assume-role from a throwaway branch). Until they pass, treat `infra` and `deploy` as open to any branch.

Later `init` runs need the same `-backend-config="bucket=<name>"` argument.

### If the subject has no ids

If a real token's subject is `repo:<owner>/<repo>:...` without the numeric ids (for example if this repository was set to the old format), every assume-role attempt is refused, because the trust names the id form. Do not loosen the trust. Either switch the repository to the immutable format in GitHub, or change `local.sub_prefix` in `ci-access/main.tf` to the name-only form and note the weaker guarantee in the decision record. The ids cannot be required separately: AWS accepts conditions on the audience and the subject for this provider, not on arbitrary token claims (`VERIFY:`).

### Prod access (the `deploy-prod` Environment, issue #268)

Prod is a second Compose stack on the same server (`_docs/decisions/2026-11-15T1840Z-dev-prod-one-server.md`). The deploy role `socboard-ci-deploy` trusts two exact subjects, `:environment:deploy` (dev, unchanged) and `:environment:deploy-prod`, both with the audience `sts.amazonaws.com` and the numeric-id prefix, as a list under one `StringEquals`. There is no pattern, so no other Environment, branch or pull request can match. The apply and plan roles, the role's permission policy and the outputs are unchanged. Decision record: `_docs/decisions/2026-11-15T2328Z-prod-deploy-trust-and-secret.md` (proposed).

- **One role serves both stacks, so AWS cannot tell a dev job from a prod job.** The deploy script's environment parameter picks the stack, not the role. The gate on prod is the `deploy-prod` Environment's required reviewer, which applies only to a job that names it. Like `deploy`, its branch limit (`main` only) is a GitHub setting, not AWS: a missing rule means any branch that names `deploy-prod` can assume the role.
- **Masking the role's unique ID.** The credentials action prints it (it starts `AROA`) in clear, and GitHub masks only registered values. Both Environments use the same role, so one repository secret, `AWS_DEPLOY_ROLE_ID`, covers both (step 5 of the task file). A workflow masks it only if it references the secret before the credentials step; that edit is in the workflows, not here. A trust change in place keeps the role's ID (`VERIFY:`).
- **Already done by the maintainer, 2026-11-16:** the empty secret `socboard/app-prod` and the Environment `deploy-prod` (reviewer: the maintainer, administrators not exempt through `can_admins_bypass`, branches limited to `main`). `can_admins_bypass` is verified as the GitHub field name (maintainer, 2026-11-16).
- **Steps for the human (not yet run):** the re-apply of this part (plan first; expected `Plan: 0 to add, 1 to change, 0 to destroy.`, the role `socboard-ci-deploy`'s trust in place), the masking secret and the refused-branch check are step-by-step in `_human-tasks/2026-11-15T2328Z-human-task-268-engineer-prod-access-apply.md`, steps 3, 5 and 6. If the first apply (above) has not run yet, it already includes this change and its counts stay as written.
- **Evidence for reviewers:** the result table in that task file, and the refused-branch run and its positive-control run, linked there by run URL. Not yet run, so both are empty (`NOT CHECKED`). The URLs of a deployed server are not live after teardown; the runs and the file are.

### Teardown (the human's step, destructive)

Destroy this part after D17b's server part, because the CI roles are what CI uses for that part, and before the state bucket (Deploy item D23). Before the state bucket is emptied, this part's state must be back in a local file. Run these in `terraform/ci-access/`. All **not yet run**.

1. Delete `backend.tf` (and commit the deletion).
   Expected: `backend.tf` is gone; `backend.tf.example` stays.
2. `terraform init -migrate-state`, answering `yes`.
   Expected: `init` says the backend was removed and copies the state to a local `terraform.tfstate`; `terraform state list` shows the 7 resources (6 if the provider is the account's own).
3. `terraform destroy`.
   Expected: "Destroy complete! Resources: 7 destroyed." If you reused the account's provider, it is not in this state and stays.

## The instance's IAM role, the snapshot schedule and the budgets

`iam-and-budgets/` defines the instance role and profile, the daily snapshot schedule of the data volume with its execution role, and three budget notifications. **The human applies it, and only the human**: an agent never runs `init`, `plan`, `apply` or `destroy`, and CI never applies it (Deploy decision 10). Apply it after `state/` and **before D17b** (the server part, issue #199, `server/` below), which needs its output.

**Status: not yet run.** Terraform and AWS were not available when this was written, so `fmt` and `validate` have run only in CI. Every "Expected" line below is what the author expects, not something seen. Every AWS argument and behaviour in `iam-and-budgets/main.tf` carries a `VERIFY:` comment until the first apply (Deploy item D19). Decision record: `_docs/decisions/2026-11-14T0907Z-instance-iam-snapshots-budgets.md` (approved).

### What it creates

- Role `socboard-instance` (only `ec2.amazonaws.com` may assume it) with the AWS-managed `AmazonSSMManagedInstanceCore` policy and one inline statement: `secretsmanager:GetSecretValue` on the two secret ARNs you set (dev's and prod's). Nothing else, no write, no other service. Instance profile `socboard-instance` wraps it.
- **The snapshot schedule lives here, not in D17b.** An Amazon Data Lifecycle Manager policy snapshots every volume tagged `project=socboard` daily at 03:00 UTC and keeps the 7 newest, so it can be applied before the volume exists. Its execution role `socboard-snapshot` (only `dlm.amazonaws.com` may assume it) is here too. The tag must be unique to this deployment. The role's AWS-managed policy is probably account-wide with no tag condition: the tag only chooses what is scheduled (`VERIFY:` the policy's current document at the first apply).
- **Rule for D17b:** the server part's root volume must not carry the tag `project=socboard`, or it is snapshotted too (unless you accept the extra snapshots knowingly). D17b's issue (#199) carries this as a criterion.
- One monthly cost budget with notifications at 50, 80 and 100 percent of actual spend, sent to your address.
- Every resource is tagged `project=socboard`. No secret is read, held or output.

### Outputs D17b reads

`instance_profile_name`: the name of the instance profile D17b attaches to the instance. Nothing else.

### The cost

A later change to the instance's permissions, the snapshot schedule or its retention is a manual apply by the human here; CI cannot do it. This is the price of keeping every IAM resource out of CI's reach. For #156 (D16): the CI apply role's deny must cover `iam:*` (including the roles `socboard-instance` and `socboard-snapshot`), `dlm:*`, `budgets:*` and, if CI holds EC2 rights, `ec2:DeleteSnapshot`, so CI cannot shorten retention or change the budget address. #156 allows one exception: the apply role may pass `socboard-instance` to `ec2.amazonaws.com` (`iam:PassRole`), and nothing else on that role.

### Steps for the human (not yet run)

Run each step in `terraform/iam-and-budgets/` with AWS credentials for your own account in your shell (never in a file in this repository). The state bucket from `state/` must already exist, and you create the Secrets Manager secret yourself, outside Terraform.

1. Copy `terraform.tfvars.example` to `terraform.tfvars` (git-ignored). Set `app_secret_arn` to your secret's ARN, `app_prod_secret_arn` to the ARN of `socboard/app-prod` (issue #268), `monthly_budget_usd` to your monthly limit and `notification_email` to your address.
   Expected: `git status` does not list `terraform.tfvars`.
2. Copy `backend.tf.example` to `backend.tf`, then run `terraform init -backend-config="bucket=<your state bucket_name>"`. Commit `backend.tf`: it holds no bucket name and no secret.
   Expected: "Successfully configured the backend "s3"!" and "Terraform has been successfully initialized!"; `.terraform.lock.hcl` appears.
3. Before committing `.terraform.lock.hcl`, add the checksums for every platform that will use it: `terraform providers lock -platform=linux_amd64 -platform=<your own platform>` (skip the second if yours is `linux_amd64`). Then commit it.
   Expected: "Success! Terraform has updated the lock file."
4. `terraform plan`, and read it: the role's inline policy must name exactly your two secret ARNs.
   Expected: "Plan: 8 to add, 0 to change, 0 to destroy." (2 roles, 2 role policy attachments, 1 inline role policy, 1 instance profile, 1 lifecycle policy, 1 budget). Stop on any surprise.
5. `terraform apply`, answering `yes`.
   Expected: "Apply complete! Resources: 8 added, 0 changed, 0 destroyed." and the output `instance_profile_name = "socboard-instance"`. The budget address may get a confirmation mail. If the snapshot lifecycle policy fails with a role error on the first apply, run `terraform apply` again: a new IAM role can take seconds to become usable (`VERIFY:`).
6. Check: in the IAM console the role `socboard-instance` has one managed policy and one inline policy; in the EC2 console under Lifecycle Manager the policy is `ENABLED`.
   Expected: as described. Whether the managed policy is wider than the agent needs is a `VERIFY:` for you to judge here. Also read the current document of `AWSDataLifecycleManagerServiceRole`: it is probably account-wide with no tag condition (`VERIFY:`).

Later `init` runs need the same `-backend-config="bucket=<name>"` argument.

### Prod secret (issue #268)

The inline policy `read-app-secret` allows `secretsmanager:GetSecretValue` on exactly two ARNs: `app_secret_arn` (dev's `socboard/app`) and the new required variable `app_prod_secret_arn` (`socboard/app-prod`, prod's). The variable accepts only the exact secret name in `us-east-1` (name, dash, the six characters Secrets Manager adds), so a wildcard, another region, `socboard/app` and the same ARN twice are refused. No other statement, action or resource is added, and no secret is read, held or output. Nothing in this part creates the secret: the maintainer created it, empty, on 2026-11-16 (its value is set by the prod deploy issue). Decision record: `_docs/decisions/2026-11-15T2328Z-prod-deploy-trust-and-secret.md` (proposed).

- **Steps for the human (not yet run):** put the secret's ARN in `terraform.tfvars` as `app_prod_secret_arn` (without printing it), then re-apply this part (plan first; expected `Plan: 0 to add, 1 to change, 0 to destroy.`, the inline policy in place, `VERIFY:`): `_human-tasks/2026-11-15T2328Z-human-task-268-engineer-prod-access-apply.md`, steps 1 and 2. If the first apply (above) has not run yet, set the variable in its step 1 and its counts stay as written.
- **Evidence for reviewers:** the result table in that task file (rows 1 and 2). Not yet run, so the apply row is empty (`NOT CHECKED`).

### Teardown (the human's step, destructive)

Destroy in this order, because the state bucket holds this part's state: D17b's server part first (it uses the instance profile), then this part, the CI-access part, and the state bucket last (Deploy item D23). Before the state bucket is emptied, this part's state must be back in a local file. Run these in `terraform/iam-and-budgets/`. All **not yet run**.

1. Delete `backend.tf` (and commit the deletion).
   Expected: `backend.tf` is gone; `backend.tf.example` stays.
2. `terraform init -migrate-state`, answering `yes`.
   Expected: `init` says the backend was removed and copies the state to a local `terraform.tfstate`; `terraform state list` shows the 8 resources.
3. `terraform destroy`.
   Expected: "Destroy complete! Resources: 8 destroyed." Snapshots the schedule already took are not deleted by this, and destroy stops their expiry, so they are billed until you delete them. The schedule copies the volume's tags to its snapshots (`copy_tags`; `VERIFY:` that it does), so list them in the EC2 console (Snapshots) filtered on the tag `project=socboard` and delete them by hand.

## The server (instance, network and data volume)

`server/` defines the one server: an EC2 instance (Ubuntu 24.04 LTS, x86_64) in the default VPC of `us-east-1`, its Elastic IP, its security group and its encrypted data volume. **The human applies it first** (Deploy item D19), after `state/`, `iam-and-budgets/` and `ci-access/`; later CI applies it on `main` with the apply role (D22b). An agent never runs `init` against the backend, `plan`, `apply` or `destroy` here.

**Status: not yet run against AWS.** `fmt`, `validate` and `terraform test` (mock provider, no credential) ran locally with Terraform 1.17.2 and run in the `terraform check` workflow. Nothing has been planned or applied, so every "Expected" line below is what the author expects, not something seen, and every AWS behaviour in `server/*.tf` carries a `VERIFY:` comment until D19. Decision record: `_docs/decisions/2026-11-14T1742Z-server-instance-network-volume.md`.

### What it creates

- One instance, `t3.small` by default (only `t3.micro`, `t3.small` and `t3.medium` are accepted), standard CPU credits, no key pair (access is through Systems Manager only), the instance profile `socboard-instance` from `iam-and-budgets/`, metadata session tokens required with a hop limit of 1 (a container on a bridge network cannot reach the instance's credentials). Its root volume is encrypted gp3, 20 GiB by default (8 to 30), and deleted with the instance. The image is the newest matching Canonical image at the first apply; later images are ignored, so a new upstream image never replaces the server on its own.
- One Elastic IP and its association with the instance (a separate resource, because the bootstrap's hostname names the address), so the address stays the same when the instance is replaced.
- The instance's user data: the bootstrap (next section), which installs Docker, the Compose plugin and Caddy and mounts the data volume.
- One security group: TCP 80 and 443 in from anywhere, TCP 80 and 443 out to anywhere, nothing else (no SSH, no app port). Opening a port means editing the list in `server/main.tf`, and the tests fail on it.
- One data volume for the database: encrypted gp3 with the AWS-managed key, 10 GiB by default (8 to 50), in the instance's zone (`us-east-1a` by default), attached with the instance stopped before any detach. It has `prevent_destroy`, so any plan that would delete or replace it fails.
- Outputs: `elastic_ip`, `hostname` (dev's), `prod_hostname`, `instance_id` and `data_volume_id`. No secret.

Every variable has a default, so no variable file is needed.

### Rules for later changes

- **The tag is a contract.** The instance, the data volume, the security group and the Elastic IP carry `project=socboard` in their create calls; the CI apply role creates nothing without it. The snapshot schedule in `iam-and-budgets/` backs up every volume with that tag (`target_tags`). Removing or renaming the data volume's tag stops the backups with no error, so a change to it needs `iam-and-budgets/` changed and applied by the human in the same sitting. The root volume must stay untagged, or it is snapshotted too: no tags in `root_block_device`, no volume tags on the instance and no provider default tags.
- **User data never holds a secret.** The user data is the bootstrap (`server/bootstrap.sh.tftpl`), which holds none. The CI plan role can read an instance's user data (`ec2:Describe*`), so a secret placed there is readable by every pull request run. The application reads its secret from Secrets Manager at run time.
- **No default VPC?** This part uses the default VPC and its default subnet in the chosen zone, and the CI apply role cannot create network resources. If the account has no default VPC in `us-east-1`, the human creates one (step 1).

### Steps for the human (not yet run)

Run each step in `terraform/server/` with AWS credentials for your own account in your shell (never in a file in this repository). `state/`, `iam-and-budgets/` and `ci-access/` must already be applied.

1. Check for a default VPC: `aws ec2 describe-vpcs --region us-east-1 --filters Name=is-default,Values=true --query 'Vpcs[].VpcId'`.
   Expected: one VPC id. If the list is empty, run `aws ec2 create-default-vpc --region us-east-1` and check again.
2. Copy `backend.tf.example` to `backend.tf`, then run `terraform init -backend-config="bucket=<your state bucket_name>"`. Commit `backend.tf`: it holds no bucket name and no secret.
   Expected: "Successfully configured the backend "s3"!" and "Terraform has been successfully initialized!"; `.terraform.lock.hcl` appears.
3. Before committing `.terraform.lock.hcl`, add the checksums for every platform that will use it: `terraform providers lock -platform=linux_amd64 -platform=<your own platform>` (skip the second if yours is `linux_amd64`). Then commit it.
   Expected: "Success! Terraform has updated the lock file."
4. `terraform plan`, and read it: the image name starts `ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-` and its owner is `099720109477`; the instance has no key name, its user data is the bootstrap and `user_data_replace_on_change` is true; four security group rules (80 and 443 each way); the root block device has no tags; the data volume has `project = "socboard"`. If this part was already applied without the bootstrap (D19a, #207), follow "The bootstrap", D19b steps, instead.
   Expected: "Plan: 10 to add, 0 to change, 0 to destroy." (instance, Elastic IP, its association, security group, four rules, data volume, attachment). Stop on any surprise.
5. `terraform apply`, answering `yes`.
   Expected: "Apply complete! Resources: 10 added, 0 changed, 0 destroyed." and the five outputs printed.
6. Check in the EC2 console: the root volume has no tags and the data volume has `project=socboard`, both encrypted; the instance's metadata shows tokens required and hop limit 1. In Systems Manager (Fleet Manager) the instance shows as online within about ten minutes.
   Expected: as described. If the instance never comes online, the egress limit (80 and 443 only) is the first suspect (`VERIFY:`); the change goes through a new issue.

Later `init` runs need the same `-backend-config="bucket=<name>"` argument.

### Teardown (the human's step, destructive)

Destroy this part first, then `iam-and-budgets/`, `ci-access/` and `state/` last (Deploy item D23). Run these in `terraform/server/`. All **not yet run**.

1. Remove the `lifecycle { prevent_destroy = true }` block from `aws_ebs_volume.data` in `server/main.tf`. This is the only manual edit teardown needs; without it `destroy` refuses. Take a last snapshot of the data volume first if you want to keep the data.
   Expected: `terraform plan -destroy` no longer reports "Instance cannot be destroyed".
2. `terraform destroy`, answering `yes`. The instance is stopped before the data volume is detached.
   Expected: "Destroy complete! Resources: 10 destroyed."
3. Snapshots outlive the destroy: the schedule's snapshots of the data volume stay (and are billed) until `iam-and-budgets/` is torn down and they are deleted by hand, as its teardown says.
   Expected: the EC2 console (Snapshots) filtered on `project=socboard` still lists them.
4. Restore the `prevent_destroy` block (or do not commit its removal) unless the deployment is gone for good.

## The bootstrap (host setup, Caddy and the data volume)

`server/bootstrap.sh.tftpl` is the server's user data (issue #233, Deploy item D17c). Terraform renders it in `server/main.tf`; cloud-init runs it once, as root, at the instance's first boot. It is part of `server/`, so it is applied with it, by the human first (D19b) and later by CI. An agent never runs `init` against the backend, `plan`, `apply` or `destroy`.

**Status: run twice on the real host.** #233's version ran at the first apply (#207, 2026-11-16); #256's hardened version ran on the replacement instance on 2026-11-16 (#236, "Steps for the human in D19b" below; the results are in `_human-tasks/2026-11-16T0033Z-human-task-236-engineer-first-apply-part-b.md`, as reported by the maintainer). That run settled, and the `VERIFY:` notes for them are removed: the three packages install on noble (Docker Compose 2.40.3, Caddy v2.11.9), the data volume is mounted by UUID at `/srv/socboard-data` (`/dev/nvme1n1`), and the bootstrap, which finds it by its `/dev/disk/by-id` link, ended `done`, `caddy/` sits on the volume with its owner and mode as described, the re-run path exists and a second run writes nothing, and after a reboot the volume is mounted with Docker and Caddy active. Every other `VERIFY:` in this section is still open (the settled ones in the comments of `server/*.tf` and `bootstrap.sh.tftpl` stay as they are, because editing them would change the user data and replace the instance): the Cloudsmith key, unattended upgrades, the hostname format, Let's Encrypt without a contact address, a missing volume not blocking the boot, the certificate re-use on a replacement, and the forwarded address with the pinned gateway.

### What it does

- **Packages:** `docker.io` and `docker-compose-v2` (the Compose plugin) from Ubuntu's own archive, signed by the Ubuntu archive key the image already trusts; `caddy` from Caddy's repository on Cloudsmith, whose downloaded keyring is accepted only when it holds exactly one primary key (its subkeys allowed) with the fingerprint `65760C51EDEA2017CEA2CA15155B6D79CA56EA34`; any other shape (no key, a second primary key, another fingerprint, a file `gpg` cannot read) stops the script before the keyring or the apt source is installed (the package names are right on noble, seen 2026-11-16; `VERIFY:` that Cloudsmith serves this key). A package is installed only when missing; the bootstrap never upgrades one (Ubuntu's unattended upgrades patch them, `VERIFY:` that they are on in the image).
- **Docker:** log rotation of 10 MB per file and 3 files per container (`/etc/docker/daemon.json`); a weekly `docker image prune --all --filter until=168h` (`/etc/cron.d/socboard-docker-prune`), which removes only images no container uses, so never one a running container uses; Docker enabled at boot, and ordered after the data volume's mount by a drop-in (`/etc/systemd/system/docker.service.d/socboard.conf`, `RequiresMountsFor=/srv/socboard-data`), so it does not start without the volume. The drop-in is written after the fstab line and the mount, when the mount unit it names exists and is active, so the `daemon-reload` accepts it; the Docker the package install already started keeps running, and the order applies from the next boot. Containers come back after a reboot only if the Compose file gives them a restart policy (D11 or D20 sets it; this part assumes `restart: unless-stopped` on `db` and `app`).
- **The data volume:** found by its volume id (`/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol...`; the bootstrap ended `done` on 2026-11-16 with the volume mounted), never by a guessed device name. It is formatted as ext4 only when `blkid -p` finds no signature at all, so a volume with data is never formatted. On a replaced instance the volume already holds ext4, so `blkid -p` exits 0 and nothing is formatted; the fstab line is written again on the new root disk with the volume's unchanged UUID. It is mounted by file-system UUID with `nofail` and `x-systemd.device-timeout=30s` at `/srv/socboard-data`. A missing volume does not block the boot: Docker and Caddy stay stopped (each has `RequiresMountsFor=/srv/socboard-data`) while Systems Manager still reaches the host (`VERIFY:` at D19b). The bare directory under the mount point is made immutable, so nothing writes to the root disk while the volume is missing, and the script stops unless the volume is mounted. The script waits up to 10 minutes for the volume, which Terraform attaches after the instance starts.
- **The layout on the volume:** three siblings, created only after the volume is mounted (`postgres-prod/`, the prod stack's, is next to `postgres/`; see "The prod stack beside dev" below). **`/srv/socboard-data/postgres`, the value `STACK_PGDATA_DIR` takes on the server** (root README, "Compose stack"; D20 sets it), owned by uid 999, the image's `postgres` user, mode 0700; Postgres writes its `pgdata` subdirectory there. `/srv/socboard-data/caddy`, Caddy's certificates and account, owned by the `caddy` user, mode 0700, outside anything a container mounts. Every run sets, each only when it differs: the mount root to `root:root` 0755 (#233's run left it owned by 999); `postgres/` to 999 and 0700 on its top level only (the database's files are never walked); `caddy/` and everything under it to the current `caddy` user, recursively, because the package creates that user again on a replaced instance and its uid may differ. `postgres-prod/` (the value `STACK_PGDATA_DIR` takes for prod, issue #266) is made the same way as `postgres/`: owned by uid 999, mode 0700, top level only, a sibling and never inside `postgres/`. Nothing on the volume is moved, copied or deleted: an existing `caddy/` stays where it is and is reused, and `postgres/` and `postgres-prod/` are created when missing. The script stops if the `caddy` user has uid 999.
- **Caddy:** `/etc/caddy/Caddyfile` has two sites sharing one global block (issue #266, "The prod stack beside dev" below). The first serves the output `hostname`, dev's (the Elastic IP through sslip.io, for example `203-0-113-10.sslip.io`, Deploy decision 6; `VERIFY:` the name format) over HTTPS and proxies to `127.0.0.1:8000` only; the second serves `prod_hostname` and proxies to `127.0.0.1:8100` only. The admin endpoint is off. The certificate comes from the ACME directory in the variable `acme_ca`: Let's Encrypt production by default, or its staging directory for testing. The account has no contact address (decision 6: a real address or none; `VERIFY:` that Let's Encrypt accepts that). Caddy's certificates and account live on the data volume (`/srv/socboard-data/caddy`), one storage for both sites, so a replaced instance keeps them, and Caddy does not start without the volume.
- **The forwarded address (for D20):** Caddy replaces any `X-Forwarded-For` the caller sent with the caller's address. The app sees the Compose network's gateway as the peer, `10.231.77.1` for the `stack` network in `compose.yaml`, whose gateway is pinned there (`gateway: 10.231.77.1`, #261). The pin is needed: without it, Docker 29.1.3 on the server gave the network the gateway `10.231.77.128`, the first address of the `ip_range` `10.231.77.128/25`, and with docker-proxy in use that gateway is the peer the app sees from host Caddy (the maintainer's measurement, 2026-11-16). `VERIFY:` measured 2026-11-16 (gateway behaviour); not re-measured in #236's run (row 19 of its record); the first deploy checks the gateway (exit 10). D20 sets `SOCBOARD_TRUSTED_PROXIES=10.231.77.1`, sets `STACK_PGDATA_DIR=/srv/socboard-data/postgres`, leaves `FORWARDED_ALLOW_IPS` at its default, and does not start the compose `proxy` service: host Caddy is the one proxy.
- **Idempotent:** each step carries its own check (a package only when missing, a file only when its content differs, a format only of a blank volume, the fstab line only when the mount point has none, an owner or mode only when it differs, a restart only when a file changed), so a second run changes nothing. A failed file write stops the script with a message naming the file; no write failure is treated as "unchanged". Re-run by hand: `sudo bash /var/lib/cloud/instance/scripts/part-001` (the path exists, and a second run on 2026-11-16 wrote nothing).
- **Never held:** no secret in the script or the Caddy file, none written, none read (no Secrets Manager call). It does not deliver `compose.yaml`, the environment file or the deploy script (D20). The security group is unchanged: TCP 80 and 443 only, no port 22.

### The prod stack beside dev (issue #266)

Dev and prod are two Compose stacks on this one server (`_docs/decisions/2026-11-15T1840Z-dev-prod-one-server.md`). This part gives prod a hostname, a loopback port and a data folder; it starts nothing for prod. The stack's own values come from `compose.yaml`'s variables (#271) and are set by the deploy script (#267). Dev's values do not change.

| Item | Dev | Prod |
|---|---|---|
| Public hostname | `<ip-with-dashes>.sslip.io` (output `hostname`) | `prod-<ip-with-dashes>.sslip.io` (output `prod_hostname`) |
| Loopback port of the app (`STACK_APP_PORT`) and Caddy's upstream | 8000 | 8100 |
| Data folder (`STACK_PGDATA_DIR`) | `/srv/socboard-data/postgres` | `/srv/socboard-data/postgres-prod` |
| Compose project (`STACK_PROJECT`) | `socboard` | `socboard-prod` |
| Stack network prefix (`STACK_NET`) | `10.231.77` | `10.231.78` |
| Pinned gateway, and so the trusted proxy (`STACK_TRUSTED_PROXIES`) | `10.231.77.1` | `10.231.78.1` |

- **Why the hostnames differ by a prefix.** Dev keeps the bare name, because the decision keeps every name of the stack already built; the prod name is derived from the same Elastic IP, so a replaced instance keeps both. `prod-<ip>.sslip.io` resolves to the address (read 2026-11-16 with `getent hosts`).
- **Why two ports.** Each stack publishes its app on the loopback only (`STACK_APP_PORT`), and host Caddy picks the stack by hostname. The tests refuse a Caddyfile in which the two sites share an upstream. The deploy script's candidate port (8001 for dev) is #267's; prod's must differ from both 8001 and 8100. The security group is unchanged: TCP 80 and 443 only.
- **The forwarded address.** Caddy replaces any `X-Forwarded-For` the caller sent, for both sites. The prod app sees its own network's gateway, `10.231.78.1`, as the peer, as dev sees `10.231.77.1`. `VERIFY:` the prod gateway when prod first runs (the deploy script's gateway check, exit 10, covers dev now and is #267's to extend to prod).
- **Certificates.** The prod hostname gets its own certificate from the same ACME account, stored beside dev's on the volume. Its first issuance counts once against Let's Encrypt's limits; dev's certificate is kept.
- **Not chosen: a sites folder on the data volume.** QA's finding F-256-6 on #256 suggested that the Caddyfile import site files from the data volume, so a later site would not replace the instance. Both options cost one replacement (the import line has to be added once), and this one adds no new writer of Caddy files as root: the bootstrap stays the only thing that writes `/etc/caddy/Caddyfile`, and nothing on the volume is read as Caddy's configuration.
- **What applying it costs.** The change is to the user data, so it replaces the instance (next section): both stacks restart, and the root disk is lost, so dev is deployed again (the next green push) and prod has nothing to restore, because it is not deployed yet. After the apply, update `DEPLOY_INSTANCE_ID` in the `deploy` Environment (and in `deploy-prod` when it holds one). Apply between deploys: a deploy that runs during the replacement fails.

### Changed user data replaces the instance

The change that adds the prod site (issue #266) is such a change: its cost is listed under "The prod stack beside dev", "What applying it costs".

`user_data_replace_on_change = true`. cloud-init runs user data once per instance, so without it a change would stop and start the same instance and never run the new script. With it, any change to the bootstrap or to `acme_ca` replaces the instance. The data volume and the Elastic IP are separate resources and survive: the old instance is stopped before the volume is detached (`stop_instance_before_detaching`), the new one mounts the same volume without formatting it, re-owns `caddy/` to its own `caddy` user, and the hostname stays. Caddy finds its certificate and ACME account for the same hostname in the kept storage and reuses them, with no new issuance (`VERIFY:` at D19b, by the certificate's serial number before and after). A replacement that does re-issue counts against Let's Encrypt's limits: 5 duplicate certificates for the same names in 7 days, and the weekly limit per registered domain, which every sslip.io user shares unless sslip.io is on the Public Suffix List (`VERIFY:` the current figures). Avoid repeated replacements in one week. What is lost is the root disk: D20's Compose file and environment file on it, so D20 deploys again after a replacement. A replacement also takes the newest Canonical image (the `ignore_changes` on `ami` applies only to an ordinary plan).

### Steps for the human in D19b

These are the steps to replace the instance of a server whose bootstrap has already run, which is what any change to `bootstrap.sh.tftpl` or `acme_ca` needs (#266 is the next one). The maintainer ran them once, on 2026-11-16 (#256's hardened bootstrap over #233's); the results are in `_human-tasks/2026-11-16T0033Z-human-task-236-engineer-first-apply-part-b.md`. The order and the commands of the sign-in, the plan and its checks, the apply, the wait, the host checks, the re-run, the reboot, the second plan and the clean-up are the ones that run used. Three things here are new and were not part of it, and each says so: the readings before the replacement (that run took none, and the root disk with its logs is gone), the certificate comparison, and the update of `DEPLOY_INSTANCE_ID`. On a first apply with the bootstrap, follow the server section's steps instead.

Run every step in your own shell, in `terraform/server/` (same backend as the server section, region `us-east-1`). Never write the AWS account number, an ARN that contains it, a token or a secret value into a file in this repository.

#### Step 1: sign in, and check the account

```
export AWS_PROFILE=socboard
```

Expected: no output. Every `aws` and `terraform` command in this shell now uses the profile `socboard`.

```
aws sso login --profile socboard --use-device-code
```

Expected: a URL and a code to enter in your browser, then "Successfully logged into Start URL".
If different: any other message means no sign-in; stop and do not run a later step, because the CLI can fall back to another profile (the 2026-11-15 incident, evidence timeline).

```
whereami() { if [ "$(aws sts get-caller-identity --query Account --output text)" = "$(aws sts get-caller-identity --profile socboard --query Account --output text)" ]; then echo "SUB-ACCOUNT ($AWS_PROFILE)"; else echo "MAIN ACCOUNT ($AWS_PROFILE)"; fi; }
```

Expected: no output. The function compares the current account with the `socboard` profile's and prints neither number.

```
whereami
```

Expected: `SUB-ACCOUNT (socboard)`.
If different: `MAIN ACCOUNT` means the shell is signed in to another account; stop and sign in again.
Risk: none, it only reads.

```
git status --short --branch
```

Expected: `## main...origin/main` and no other line: an up-to-date clean clone.

#### Step 2: record what the replacement changes (new)

This step reads the old instance before it is deleted. Set the instance id and define the helper once. `onserver` sends one shell command to the instance through Systems Manager and prints its output (the maintainer's helper, verified on 2026-11-16):

```
IID=$(terraform output -raw instance_id)
```

Expected: no output. `IID` holds the current instance id; step 4 sets it again after the apply.

```
onserver() { CID=$(aws ssm send-command --region us-east-1 --instance-ids "$IID" --document-name AWS-RunShellScript --parameters "{\"commands\":[\"$1\"]}" --query Command.CommandId --output text); aws ssm wait command-executed --region us-east-1 --command-id "$CID" --instance-id "$IID" 2>/dev/null; aws ssm get-command-invocation --region us-east-1 --command-id "$CID" --instance-id "$IID" --query StandardOutputContent --output text; }
```

Expected: no output. The text given to `onserver` must hold no double quote, because it goes into a JSON list. The wait gives up after about 100 seconds; if a command such as `cloud-init status --wait` runs longer, the output is empty or partial: run `aws ssm get-command-invocation --region us-east-1 --command-id "$CID" --instance-id "$IID" --query StandardOutputContent --output text` again until it prints the result.

```
terraform output
```

Expected: `elastic_ip`, `hostname`, `instance_id` and `data_volume_id` (and `prod_hostname`, once the change of #266 has been applied). Keep them: step 5 compares them with the output after the apply.
If different: fewer than four means the backend is not initialised (the server section's step 2); stop.

```
echo | openssl s_client -connect "$(terraform output -raw hostname):443" -servername "$(terraform output -raw hostname)" 2>/dev/null | openssl x509 -noout -serial -issuer -dates
```

Expected: `serial=`, an issuer that names Let's Encrypt, and two dates. Keep the serial: step 8 compares it. No output means no certificate is served yet: write "none".

```
onserver 'tail -n 5 /var/log/cloud-init-output.log'
```

Expected: the last line is `socboard-bootstrap: done`.

```
onserver 'dpkg-query -W docker.io docker-compose-v2 caddy'
```

Expected: three package names with versions. Keep them.

```
onserver 'stat -c %U:%u:%a:%n /srv/socboard-data /srv/socboard-data/postgres /srv/socboard-data/caddy'
```

Expected: `owner:uid:mode:path` for each folder. A host bootstrapped by #256 or later shows `root:0:755` for the mount root, uid 999 and `700` for `postgres/`, and the `caddy` user and `700` for `caddy/`. A host bootstrapped by #233 alone shows the mount root owned by 999 and no `postgres/`; the new bootstrap fixes that.

```
onserver 'findmnt -no UUID /srv/socboard-data; id -u caddy'
```

Expected: the volume's file-system UUID, then the `caddy` user's uid. Keep both: step 7 compares the UUID, and the new instance may give `caddy` another uid (the bootstrap re-owns `caddy/`).
If different: if `findmnt` prints nothing, the volume is not mounted; stop, the replacement must wait.
Risk: none, these only read.

#### Step 3: plan, and read it

```
terraform plan -out=d19b.plan -no-color > /tmp/d19b-plan.txt
```

Expected: no output on the screen; the plan is in `/tmp/d19b-plan.txt` and in `d19b.plan` (git-ignored by `*.plan`).
If different: an error means no plan exists. Do not go on; send the error text.

```
grep -E "must be replaced|will be destroyed|will be created|^Plan:" /tmp/d19b-plan.txt
```

Expected: `aws_instance.server` "must be replaced" because of `user_data`, with `aws_volume_attachment.data` and `aws_eip_association.server` replaced with it.
- On 2026-11-16 the last line was "Plan: 4 to add, 0 to change, 3 to destroy.": the three replaced resources and `aws_ssm_document.deploy`, the `socboard-deploy` document, created for the first time.
- Once that document exists, a replacement alone reads "Plan: 3 to add, 0 to change, 3 to destroy." A pending change to `socboard-deploy.sh` adds "1 to change" for the document. So read the list of resources, not only the counts.

```
grep -E "aws_ebs_volume\.data|aws_eip\.server" /tmp/d19b-plan.txt | grep -E "destroy|replace"
```

Expected: no output. This is the stop rule: the data volume and the Elastic IP are neither destroyed nor replaced.
If different: any line printed means do not apply. Refusal test: a plan that would destroy or replace the data volume also fails at the plan with "Instance cannot be destroyed" (its `prevent_destroy`); that is the expected refusal, not a fault to work around. Delete the plan with `rm -f d19b.plan` and send what the lines say.
Risk: none, it only reads.

#### Step 4: apply the saved plan

```
terraform apply d19b.plan
```

Expected: "Apply complete!" with the counts the plan showed (2026-11-16: 4 added, 0 changed, 3 destroyed) and the outputs. A saved plan applies without a prompt: step 3's checks are the confirmation.

```
IID=$(terraform output -raw instance_id)
```

Expected: no output. `IID` now holds the new instance's id, so `onserver` and the later steps reach the new instance. Run this block again after any other apply that replaces the instance, such as the staging apply below.
If different: if the apply stops with an error, do not retry another way; send the error text. If no certificate is issued within 15 minutes (step 8 shows no valid certificate, and Caddy's journal, read with `onserver 'journalctl -u caddy --no-pager | tail -n 30'` once `IID` is set as above, shows ACME errors; the first attempts can fail while the Elastic IP's association is still moving), apply once more with the staging directory to find the cause without using up production attempts, then stop and ask for a new issue:

```
terraform apply -var acme_ca=https://acme-staging-v02.api.letsencrypt.org/directory
```

Risk: replaces the instance. The old root disk and every file on it are deleted (Compose files, the environment file, the images and the logs); the data volume and the Elastic IP stay. Both stacks on the host (dev, and prod once it exists) are down until they are deployed again (D20's deploy). The staging command is a second replacement. Let's Encrypt allows 5 duplicate certificates for the same names in 7 days and shares a weekly limit per registered domain with every sslip.io user (`VERIFY:` the current figures), so do not replace more than once or twice in a week. Undo: none; an earlier bootstrap comes back only by reverting the change and replacing again.

#### Step 5: check the outputs

```
terraform output
```

Expected: `elastic_ip`, `hostname` and `data_volume_id` equal step 2's; `instance_id` is new (2026-11-16: `i-0a09e0f4996d70859`).
If different: a changed `elastic_ip` or `hostname` means the address was replaced; stop, because the hostname and the certificate no longer match.


#### Step 6: wait for the instance to come online

```
for i in $(seq 60); do s=$(aws ssm describe-instance-information --region us-east-1 --filters "Key=InstanceIds,Values=$IID" --query 'InstanceInformationList[].PingStatus' --output text); echo "$s"; [ "$s" = Online ] && break; sleep 10; done
```

Expected: `Online` as the last line, on the first poll or within about ten minutes (2026-11-16: the first poll). Empty lines before it are normal while the agent starts.
If different: ten minutes without `Online` (60 polls end) means the egress limit (TCP 80 and 443 only) is the first suspect (`VERIFY:`); the fix goes through a new issue.
Risk: none, it only reads.

#### Step 7: check the host

```
onserver 'cloud-init status --wait; tail -n 1 /var/log/cloud-init-output.log'
```

If the output is empty, the wait ran out before cloud-init finished; read the result again as step 2 says.

Expected: `status: done`, then `socboard-bootstrap: done`.

```
onserver 'grep -c formatting /var/log/cloud-init-output.log; true'
```

Expected: `0`. A replacement never formats the volume, which already holds a file system.
If different: `1` means the volume was seen as blank and formatted; stop, send the log, and start no stack.

```
onserver 'findmnt /srv/socboard-data; findmnt -no UUID /srv/socboard-data'
```

Expected: the volume's NVMe device (2026-11-16: `/dev/nvme1n1`), and the same UUID as step 2's. The fstab line is by UUID.

```
onserver 'stat -c %U:%a:%n /srv/socboard-data /srv/socboard-data/postgres /srv/socboard-data/caddy'
```

Expected: `root:755` for `/srv/socboard-data`; `700` for `postgres/` owned by uid 999 (the host names it `dnsmasq`; nothing on the host runs as that uid); `caddy` and `700` for `caddy/`.

```
onserver 'systemctl is-active docker caddy'
```

Expected: `active` twice.

```
onserver 'docker compose version --short'
```

Expected: a version (2026-11-16: 2.40.3).

```
onserver 'caddy version'
```

Expected: a version (2026-11-16: v2.11.9).

#### Step 8: check from outside

```
curl -sS -o /dev/null -w '%{http_code}' "https://$(terraform output -raw hostname)/"
```

Expected: `502` while nothing is deployed (Caddy answers, nothing is on port 8000), with no certificate warning. Once the app is deployed again it answers with the app's own status.
If different: a certificate error or a timeout means Caddy has not got a certificate yet; wait five minutes and try again, then follow step 4's staging path.

```
echo | openssl s_client -connect "$(terraform output -raw hostname):443" -servername "$(terraform output -raw hostname)" 2>/dev/null | openssl x509 -noout -serial -issuer -dates
```

Expected (new): the same `serial=` as step 2: Caddy found its certificate and account in the kept storage and issued nothing new. A different serial is not a failure: note it with the time the issuance took and any early ACME failure, because it counts against Let's Encrypt's limits.

#### Step 9: run the bootstrap a second time

```
onserver 'bash /var/lib/cloud/instance/scripts/part-001 > /tmp/socboard-rerun.log 2>&1; grep -c -w wrote /tmp/socboard-rerun.log; tail -n 1 /tmp/socboard-rerun.log'
```

Expected: `0` (no line with the word `wrote`), then `socboard-bootstrap: done` (2026-11-16: it wrote nothing and ended `done`).
If different: a count above 0 means a step is not idempotent; stop and send `onserver 'cat /tmp/socboard-rerun.log'`.
Risk: none when it behaves; a step that is not idempotent rewrites a file and restarts a service. Undo: none; the next replacement starts from the template again.

#### Step 10: update the instance id that CI deploys to (new)

The instance id changed, so every GitHub Environment variable that holds it is stale. `DEPLOY_INSTANCE_ID` on the `deploy` Environment (#238) is one; `DEPLOY_URL` does not change, because the hostname does not. Until the id is updated, every deploy fails at the send step with "the instance id in DEPLOY_INSTANCE_ID may be stale".

```
gh variable list --env deploy --repo andrew-chung-au/socboard
```

Expected: a list that includes `DEPLOY_INSTANCE_ID` once #238's steps have run. When prod exists, check its Environment too, and update every Environment that holds the id:

```
gh variable list --env deploy-prod --repo andrew-chung-au/socboard
```

Expected: `DEPLOY_INSTANCE_ID` listed once prod's deploy exists; an error that the Environment does not exist means prod is not set up yet, and there is nothing to update.
If different: if the variable is not listed, #238's first run has not happened; skip the next block, because #238's own steps read the current output.

```
gh variable set DEPLOY_INSTANCE_ID --env deploy --repo andrew-chung-au/socboard --body "$(terraform output -raw instance_id)"
```

Expected: no error. List the variables again: `DEPLOY_INSTANCE_ID` is there.
If different: an authentication error means `gh` is not signed in as a repository admin; use Settings, Environments in the browser and type the id from step 5.
Risk: a wrong value fails the next deploy job before any AWS call, naming the variable. Undo: set it again.

#### Step 11: reboot

```
aws ec2 reboot-instances --region us-east-1 --instance-ids "$IID"
```

Expected: no output. Wait a minute, then repeat step 6's command until the last line is `Online`.
Risk: both stacks are down for about two minutes. Undo: none needed. Once a stack is deployed, its containers come back only with `restart: unless-stopped` in the Compose file.

```
onserver 'findmnt /srv/socboard-data; systemctl is-active docker caddy'
```

Expected: the volume mounted, then `active` twice (2026-11-16: as reported).
If different: if the volume is not mounted, Docker and Caddy stay stopped on purpose (`RequiresMountsFor`) while Systems Manager still reaches the host; stop and send `onserver 'systemctl status docker --no-pager'`.

#### Step 12: a second plan, and clean-up

```
terraform plan
```

Expected: "No changes."
If different: stop and send the plan. Do not apply. After a staging apply in step 4 the plan wants to set `acme_ca` back; applying that is another replacement, so do it only when you mean it.

```
rm -f d19b.plan
```

Expected: no output. The saved plan is gone; `/tmp/d19b-plan.txt` can stay or go.

```
git status --short
```

Expected: no output; `backend.tf` and `.terraform.lock.hcl` are unchanged.

#### Step 13: record the result once

Write the results (date, what was checked, result, link) into a dated file in `_human-tasks/` named for the issue that asked for the replacement (the suggested timestamp for its name is the output of the command below), with a row for each step above, and put one line in `_docs/records/2026-11-16T0000Z-measure-planning-deploy-evidence-timeline.md` that links it. Include the old and new instance ids, the plan counts, the old and new certificate serials and the readings from step 2. The record holds no account number, no ARN with it, no e-mail address and no secret.

```
date -u +%Y-%m-%dT%H%MZ
```

Expected: a timestamp such as `2026-11-16T0033Z`; the file is `_human-tasks/<timestamp>-human-task-<issue number>-engineer-<slug>.md`.

In the record, each row is PASS, FAIL or NOT CHECKED. A FAIL says what you saw, and the fix goes through a new issue, never an edit during the apply. A fix to the bootstrap replaces the instance again, so these steps run again from step 2, and the record says why.

#### What changes in these steps for the prod change (issue #266)

For the replacement that adds the prod site, this is what differs. It is the maintainer's apply. It ran on 2026-11-16 before #237's first deploy, and that is fine: with nothing running there is nothing to restart, and `DEPLOY_INSTANCE_ID` was not set yet, so there was no instance id to update (#238's steps set it from the current output). Once stacks are running, apply between deploys.

- Step 2: `terraform output` shows four values, because `prod_hostname` does not exist yet.
- Step 3: the counts are the usual ones (3/0/3 once the deploy document exists); the plan also lists "Changes to Outputs" with `prod_hostname`. The stop rule is unchanged.
- Step 5: the output now has five values, with `prod_hostname` equal to `prod-` and the dev hostname.
- Step 7: after the `stat` check add the prod folder, then the Caddyfile check. The expected prod folder is uid 999 and `700`:

```
onserver 'stat -c %U:%a:%n /srv/socboard-data/postgres-prod'
```

Expected: `dnsmasq:700:/srv/socboard-data/postgres-prod` (the host names uid 999 `dnsmasq`).
If different: another owner or mode means the bootstrap did not finish; stop and send the output of `onserver 'tail -n 20 /var/log/cloud-init-output.log'`.

```
onserver 'grep -c reverse_proxy /etc/caddy/Caddyfile'
```

Expected: `2`, one per site.
If different: `1` means the prod site is missing; stop and send the output.

This check printed `Valid configuration` on 2026-11-16 (as reported):

```
onserver 'runuser -u caddy -- caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -n 1'
```

Expected: `Valid configuration`. If different: send the line printed; do not edit the file on the host.
- Step 8: check the prod hostname as well, and compare the dev certificate's serial with step 2's (it must be unchanged: the kept storage is reused). The prod hostname gets a new certificate, so its serial is new:

```
curl -sS -o /dev/null -w '%{http_code}' "https://$(terraform output -raw prod_hostname)/"
```

Expected: `502` with no certificate warning: Caddy has the second site and nothing is on port 8100.
If different: a certificate error for prod alone means its issuance has not finished; wait five minutes and try again, then read Caddy's journal as step 4 says.
- Step 10: update every Environment that holds the instance id, `deploy-prod` included once it has the variable.

### Teardown

Nothing separate: the bootstrap goes with the instance (server teardown above). Caddy's certificate data stays on the data volume until the volume itself is destroyed.

## The deploy document (`socboard-deploy`)

`server/deploy_document.tf` creates one Systems Manager document, `socboard-deploy` (type `Command`), whose content is `server/socboard-deploy.sh` as written (issue #237, Deploy item D20). It has one parameter, `digest`, an image digest (`sha256:` and 64 lower-case hex digits). The pattern is checked by the document at `SendCommand` and again by the script. It is part of `server/`, so the human applies it first and `terraform plan` shows only the document added (`Plan: 1 to add, 0 to change, 0 to destroy.`); the instance is not replaced, because its user data is untouched.

**Status: not yet run.** `terraform test` (mock provider) checks the document's shape, and the script is tested offline with stub programs (`tests/test_deploy_script.py`). Nothing has been applied or run on the server, so every AWS and Ubuntu detail carries `VERIFY:` in the files until the first deploy.

- **Who may do what.** CI's apply role may change the document (`ManageDeployDocument` in `ci-access/apply.tf`), so once CI applies `server/` (D22b), whoever approves an `infra` apply can change what runs as root with the application secret. The deploy role may only send this one document (it can send no AWS-owned document, so no generic shell) and read a command's result.
- **A change to the script is an update of the document.** Edit `socboard-deploy.sh`, then plan and apply `server/`; the plan shows the document changed in place. The script is the whole content: it fetches no script at run time.
- **What the script does, in order:** takes a lock, checks the digest, stops with "already deployed" if that image is running healthy, pulls the image by digest and checks its labels, fetches `compose.yaml` and `compose/db_init.py` of the image's own revision, pulls the pinned Postgres image (`compose pull db`, by digest, from Docker Hub; it runs with placeholder values for the variables `compose.yaml` requires, because the secret is not read yet), reads the secret `socboard/app`, writes the variable file in `/run/socboard-deploy` (a memory file system, deleted at exit), starts `db`, checks the stack network's gateway against the expected proxy address, runs `db-init`, `migrate` and `seed`, proves the image as a candidate on `127.0.0.1:8001`, swaps the `app` container, checks the new release (health, the full smoke check, host Caddy) and restores the previous image if a check fails. It never touches Caddy, runs no SQL and undoes no migration.
- **Exit codes** (the fixed text of each is in the script): 2 refused digest, 3 secret not usable, 4 image, database image or files, 5 `db-init`, `migrate` or `seed`, 6 candidate not healthy, 7 failed after the swap (restored, or nothing to restore), 8 another run in progress, 9 failed and not restored, 10 the stack network's gateway is not the expected proxy address.
- **The proxy address.** The script sets `SOCBOARD_TRUSTED_PROXIES` to the expected `10.231.77.1` through an override file it writes next to `compose.yaml`, and stops with exit 10 if the stack network's gateway is another address. This needs the gateway pinned in `compose.yaml`; the first deploy waits for that change to merge.
- **The secret** is set by the human (`put-secret-value` from a file made on the human's own machine); the script never creates it. The task file `_human-tasks/*-human-task-237-engineer-first-deploy.md` has the steps.
- **Teardown:** nothing separate; `terraform destroy` of `server/` removes the document.
