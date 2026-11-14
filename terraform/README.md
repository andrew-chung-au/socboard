# Terraform: the state bucket

`state/` defines one private S3 bucket in `us-east-1` that later holds Terraform state. The human applies it once. An agent never runs `apply`, `destroy` or any step below.

**Status: not yet run.** Terraform and AWS were not available when this was written. Every "Expected" line below is what the author expects, not something seen. Every argument name in `state/main.tf` carries a `VERIFY:` comment until the first `init`, `validate` and apply (Deploy item D19).

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

**Status: not yet run.** Terraform and AWS were not available when this was written, so `fmt` and `validate` have not run anywhere yet; the `terraform check` workflow runs them on the first pull request. Every "Expected" line below is what the author expects, not something seen. Every AWS argument and behaviour in `ci-access/*.tf` carries a `VERIFY:` comment until the first apply (Deploy item D19). Decision record: `_docs/decisions/2026-11-14T0928Z-ci-oidc-roles.md` (proposed).

### What it creates

| Role | Assumed by | May do | Never |
|---|---|---|---|
| `socboard-ci-plan` | any pull request run in this repository (subject ends `:pull_request`) | read the server state under `server/` in the state bucket, `ec2:Describe*`, read the deploy document | write anything, any `iam:*`, `dlm:*` or `budgets:*` action, read a secret value |
| `socboard-ci-apply` | a job in the `infra` Environment (subject ends `:environment:infra`) | read and write the server state under `server/`, create EC2 resources in `us-east-1` only with the tag `project=socboard`, change only resources that carry it, manage the one deploy document, pass the instance role to EC2 | any IAM action but that one PassRole (explicit deny on `iam:*`, so it cannot change its own role or policy, or pass any other role), any `dlm:*` or `budgets:*` action, tag an existing untagged resource, read a secret value |
| `socboard-ci-deploy` | a job in the `deploy` Environment (subject ends `:environment:deploy`) | send the one deploy document to instances tagged `project=socboard`, read a command's result by its id | run any AWS-owned document (so no generic shell), any `iam:*`, `dlm:*` or `budgets:*` action, read a secret value |

- The provider is made here unless `reuse_oidc_provider_arn` is set. AWS allows one provider per URL in an account, so an account that already has one must reuse it.
- Every trust condition requires the audience `sts.amazonaws.com` and one exact subject. The subject starts with `repo:<owner>@<owner id>/<repo>@<repo id>`, which is the form GitHub reported for this repository on 2026-11-14 (its API said `use_immutable_subject: true`). The numeric ids are therefore part of every trust, and a repository that was renamed or recreated cannot reuse a role. `VERIFY:` that a real token has this shape (D19).
- **The branch limit is not in AWS.** For a job that names an Environment, the subject carries the Environment name, not the branch. A workflow on any branch that names `infra` or `deploy` could assume the role unless the Environment lets only `main` deploy. That is the human's setting in GitHub (D19), and the check in `_human-tasks/2026-11-14T0928Z-human-task-156-engineer-ci-access-apply-and-branch-check.md` is the evidence. The Engineer has not checked it.
- **The plan role is open to every pull request**, including a pull request that edits a workflow. That is why it is read-only and cannot read a secret value. Pull requests from forks get no OIDC token from GitHub by default (`VERIFY:` in D18b).
- The state bucket is shared by every part. The plan and apply roles reach only the `server/` prefix, so the CI-applied server part (D17b) must set its backend `key` under `server/`. The state of this part and of `iam-and-budgets/` lives outside that prefix, so the CI roles can neither read nor change it. A read-only plan cannot write the native lock file, so the plan job runs with `-lock=false` (D18b).
- The apply role's EC2 list is a first guess at D17b's needs. If the first CI apply is refused an action, the human adds that one action and applies this part by hand. That is the intended cost of keeping IAM out of CI's reach.
- **CI makes no IAM change.** The only IAM action any CI role may take is the apply role passing `socboard-instance` to `ec2.amazonaws.com`. Two explicit denies do it: `iam:*` on every resource except the instance role, and every action except `iam:PassRole` on the instance role. (A plain `Deny iam:*` would also block that PassRole, since an explicit deny always wins.) IAM reads are denied too, so D17b must name the instance profile directly and use no IAM data source. The plan and deploy roles deny `iam:*` outright. All three deny `dlm:*` and `budgets:*`: the snapshot schedule and the budgets are human-applied (#162).
- The apply role's EC2 writes are tag-scoped. D17b must put `project=socboard` on the instance, its volumes (`volume_tags`), the security group and the Elastic IP in the create call; tags can be added afterwards only to a resource that already has it. AWS cannot tag-scope the reads (`ec2:Describe*`), the image, subnet and network interface a launch refers to, the VPC a security group is made in, a new security group rule, or the network interface named when an Elastic IP is moved; each of those calls also names a tagged resource. The role can still change the tagged instance itself, including its user data, which is how D17b manages the server.
- The deploy role reads results with `ssm:GetCommandInvocation` only. AWS authorises it on `*` with no condition key, so it cannot be limited to the deploy document or the instance; the role needs a command id and cannot list other commands.
- The deploy document is created by the CI-applied server part (D17b or D17c), named `socboard-deploy`. This part only names it. The pattern that checks the image digest lives inside the document, not in this role's policy.
- No access key, user or secret exists here. No `data` source reads a secret, and no output holds a secret value. The three outputs are role ARNs.

### Outputs

`plan_role_arn`, `apply_role_arn` and `deploy_role_arn`. The workflows (D18b, D22a, D22b) take them as repository variables, not secrets.

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
5. `terraform plan`, and read it. Check that each role's trust names the audience and exactly one subject, that the apply role's policy has the deny statements, and that the deploy role's policy names no AWS-owned document.
   Expected: "Plan: 7 to add, 0 to change, 0 to destroy." (the provider, three roles and three inline role policies; 6 if you reuse the account's provider). Stop on any surprise.
6. `terraform apply`, answering `yes`.
   Expected: "Apply complete! Resources: 7 added, 0 changed, 0 destroyed." (6 if you reuse the provider) and three role ARNs printed.
7. Do the checks in the human task file named above (the Environments' branch limit and the refused assume-role from a throwaway branch). Until they pass, treat `infra` and `deploy` as open to any branch.

Later `init` runs need the same `-backend-config="bucket=<name>"` argument.

### If the subject has no ids

If a real token's subject is `repo:<owner>/<repo>:...` without the numeric ids (for example if this repository was set to the old format), every assume-role attempt is refused, because the trust names the id form. Do not loosen the trust. Either switch the repository to the immutable format in GitHub, or change `local.sub_prefix` in `ci-access/main.tf` to the name-only form and note the weaker guarantee in the decision record. The ids cannot be required separately: AWS accepts conditions on the audience and the subject for this provider, not on arbitrary token claims (`VERIFY:`).

### Teardown (the human's step, destructive)

Destroy this part after D17b's server part, because the CI roles are what CI uses for that part, and before the state bucket (Deploy item D23). Before the state bucket is emptied, this part's state must be back in a local file. Run these in `terraform/ci-access/`. All **not yet run**.

1. Delete `backend.tf` (and commit the deletion).
   Expected: `backend.tf` is gone; `backend.tf.example` stays.
2. `terraform init -migrate-state`, answering `yes`.
   Expected: `init` says the backend was removed and copies the state to a local `terraform.tfstate`; `terraform state list` shows the 7 resources (6 if the provider is the account's own).
3. `terraform destroy`.
   Expected: "Destroy complete! Resources: 7 destroyed." If you reused the account's provider, it is not in this state and stays.

## The instance's IAM role, the snapshot schedule and the budgets

`iam-and-budgets/` defines the instance role and profile, the daily snapshot schedule of the data volume with its execution role, and three budget notifications. **The human applies it, and only the human**: an agent never runs `init`, `plan`, `apply` or `destroy`, and CI never applies it (Deploy decision 10). Apply it after `state/` and **before D17b** (the CI-applied server part, not filed yet), which needs its output.

**Status: not yet run.** Terraform and AWS were not available when this was written, so `fmt` and `validate` have run only in CI. Every "Expected" line below is what the author expects, not something seen. Every AWS argument and behaviour in `iam-and-budgets/main.tf` carries a `VERIFY:` comment until the first apply (Deploy item D19). Decision record: `_docs/decisions/2026-11-14T0907Z-instance-iam-snapshots-budgets.md` (proposed).

### What it creates

- Role `socboard-instance` (only `ec2.amazonaws.com` may assume it) with the AWS-managed `AmazonSSMManagedInstanceCore` policy and one inline statement: `secretsmanager:GetSecretValue` on the one secret ARN you set. Nothing else, no write, no other service. Instance profile `socboard-instance` wraps it.
- **The snapshot schedule lives here, not in D17b.** An Amazon Data Lifecycle Manager policy snapshots every volume tagged `project=socboard` daily at 03:00 UTC and keeps the 7 newest, so it can be applied before the volume exists. Its execution role `socboard-snapshot` (only `dlm.amazonaws.com` may assume it) is here too. The tag must be unique to this deployment. The role's AWS-managed policy is probably account-wide with no tag condition: the tag only chooses what is scheduled (`VERIFY:` the policy's current document at the first apply).
- **Rule for D17b:** the server part's root volume must not carry the tag `project=socboard`, or it is snapshotted too (unless you accept the extra snapshots knowingly). D17b's issue must carry this as a criterion.
- One monthly cost budget with notifications at 50, 80 and 100 percent of actual spend, sent to your address.
- Every resource is tagged `project=socboard`. No secret is read, held or output.

### Outputs D17b reads

`instance_profile_name`: the name of the instance profile D17b attaches to the instance. Nothing else.

### The cost

A later change to the instance's permissions, the snapshot schedule or its retention is a manual apply by the human here; CI cannot do it. This is the price of keeping every IAM resource out of CI's reach. For #156 (D16): the CI apply role's deny must cover `iam:*` (including the roles `socboard-instance` and `socboard-snapshot`), `dlm:*`, `budgets:*` and, if CI holds EC2 rights, `ec2:DeleteSnapshot`, so CI cannot shorten retention or change the budget address.

### Steps for the human (not yet run)

Run each step in `terraform/iam-and-budgets/` with AWS credentials for your own account in your shell (never in a file in this repository). The state bucket from `state/` must already exist, and you create the Secrets Manager secret yourself, outside Terraform.

1. Copy `terraform.tfvars.example` to `terraform.tfvars` (git-ignored). Set `app_secret_arn` to your secret's ARN, `monthly_budget_usd` to your monthly limit and `notification_email` to your address.
   Expected: `git status` does not list `terraform.tfvars`.
2. Copy `backend.tf.example` to `backend.tf`, then run `terraform init -backend-config="bucket=<your state bucket_name>"`. Commit `backend.tf`: it holds no bucket name and no secret.
   Expected: "Successfully configured the backend "s3"!" and "Terraform has been successfully initialized!"; `.terraform.lock.hcl` appears.
3. Before committing `.terraform.lock.hcl`, add the checksums for every platform that will use it: `terraform providers lock -platform=linux_amd64 -platform=<your own platform>` (skip the second if yours is `linux_amd64`). Then commit it.
   Expected: "Success! Terraform has updated the lock file."
4. `terraform plan`, and read it: the role's inline policy must name exactly your one secret ARN.
   Expected: "Plan: 8 to add, 0 to change, 0 to destroy." (2 roles, 2 role policy attachments, 1 inline role policy, 1 instance profile, 1 lifecycle policy, 1 budget). Stop on any surprise.
5. `terraform apply`, answering `yes`.
   Expected: "Apply complete! Resources: 8 added, 0 changed, 0 destroyed." and the output `instance_profile_name = "socboard-instance"`. The budget address may get a confirmation mail. If the snapshot lifecycle policy fails with a role error on the first apply, run `terraform apply` again: a new IAM role can take seconds to become usable (`VERIFY:`).
6. Check: in the IAM console the role `socboard-instance` has one managed policy and one inline policy; in the EC2 console under Lifecycle Manager the policy is `ENABLED`.
   Expected: as described. Whether the managed policy is wider than the agent needs is a `VERIFY:` for you to judge here. Also read the current document of `AWSDataLifecycleManagerServiceRole`: it is probably account-wide with no tag condition (`VERIFY:`).

Later `init` runs need the same `-backend-config="bucket=<name>"` argument.

### Teardown (the human's step, destructive)

Destroy in this order, because the state bucket holds this part's state: D17b's server part first (it uses the instance profile), then this part, the CI-access part, and the state bucket last (Deploy item D23). Before the state bucket is emptied, this part's state must be back in a local file. Run these in `terraform/iam-and-budgets/`. All **not yet run**.

1. Delete `backend.tf` (and commit the deletion).
   Expected: `backend.tf` is gone; `backend.tf.example` stays.
2. `terraform init -migrate-state`, answering `yes`.
   Expected: `init` says the backend was removed and copies the state to a local `terraform.tfstate`; `terraform state list` shows the 8 resources.
3. `terraform destroy`.
   Expected: "Destroy complete! Resources: 8 destroyed." Snapshots the schedule already took are not deleted by this, and destroy stops their expiry, so they are billed until you delete them. The schedule copies the volume's tags to its snapshots (`copy_tags`; `VERIFY:` that it does), so list them in the EC2 console (Snapshots) filtered on the tag `project=socboard` and delete them by hand.
