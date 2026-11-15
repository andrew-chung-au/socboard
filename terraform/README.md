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
| `socboard-ci-deploy` | a job in the `deploy` Environment (subject ends `:environment:deploy`) | send the one deploy document to instances tagged `project=socboard`, read a command's result by its id | run any AWS-owned document (so no generic shell), any `iam:*`, `dlm:*` or `budgets:*` action, read a secret value |

- The provider is made here unless `reuse_oidc_provider_arn` is set. AWS allows one provider per URL in an account, so an account that already has one must reuse it.
- Every trust condition requires the audience `sts.amazonaws.com` and one exact subject. The subject starts with `repo:<owner>@<owner id>/<repo>@<repo id>`, which is the form GitHub reported for this repository on 2026-11-14 (its API said `use_immutable_subject: true`). The numeric ids are therefore part of every trust, and a repository that was renamed or recreated cannot reuse a role. `VERIFY:` that a real token has this shape (D19).
- **The branch limit is not in AWS.** For a job that names an Environment, the subject carries the Environment name, not the branch. A workflow on any branch that names `infra` or `deploy` could assume the role unless the Environment lets only `main` deploy. That is the human's setting in GitHub (D19), and the check in `_human-tasks/2026-11-14T0928Z-human-task-156-engineer-ci-access-apply-and-branch-check.md` is the evidence. The Engineer has not checked it.
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

`iam-and-budgets/` defines the instance role and profile, the daily snapshot schedule of the data volume with its execution role, and three budget notifications. **The human applies it, and only the human**: an agent never runs `init`, `plan`, `apply` or `destroy`, and CI never applies it (Deploy decision 10). Apply it after `state/` and **before D17b** (the server part, issue #199, `server/` below), which needs its output.

**Status: not yet run.** Terraform and AWS were not available when this was written, so `fmt` and `validate` have run only in CI. Every "Expected" line below is what the author expects, not something seen. Every AWS argument and behaviour in `iam-and-budgets/main.tf` carries a `VERIFY:` comment until the first apply (Deploy item D19). Decision record: `_docs/decisions/2026-11-14T0907Z-instance-iam-snapshots-budgets.md` (approved).

### What it creates

- Role `socboard-instance` (only `ec2.amazonaws.com` may assume it) with the AWS-managed `AmazonSSMManagedInstanceCore` policy and one inline statement: `secretsmanager:GetSecretValue` on the one secret ARN you set. Nothing else, no write, no other service. Instance profile `socboard-instance` wraps it.
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

## The server (instance, network and data volume)

`server/` defines the one server: an EC2 instance (Ubuntu 24.04 LTS, x86_64) in the default VPC of `us-east-1`, its Elastic IP, its security group and its encrypted data volume. **The human applies it first** (Deploy item D19), after `state/`, `iam-and-budgets/` and `ci-access/`; later CI applies it on `main` with the apply role (D22b). An agent never runs `init` against the backend, `plan`, `apply` or `destroy` here.

**Status: not yet run against AWS.** `fmt`, `validate` and `terraform test` (mock provider, no credential) ran locally with Terraform 1.17.2 and run in the `terraform check` workflow. Nothing has been planned or applied, so every "Expected" line below is what the author expects, not something seen, and every AWS behaviour in `server/*.tf` carries a `VERIFY:` comment until D19. Decision record: `_docs/decisions/2026-11-14T1742Z-server-instance-network-volume.md`.

### What it creates

- One instance, `t3.small` by default (only `t3.micro`, `t3.small` and `t3.medium` are accepted), standard CPU credits, no key pair (access is through Systems Manager only), the instance profile `socboard-instance` from `iam-and-budgets/`, metadata session tokens required with a hop limit of 1 (a container on a bridge network cannot reach the instance's credentials). Its root volume is encrypted gp3, 20 GiB by default (8 to 30), and deleted with the instance. The image is the newest matching Canonical image at the first apply; later images are ignored, so a new upstream image never replaces the server on its own.
- One Elastic IP and its association with the instance (a separate resource, because the bootstrap's hostname names the address), so the address stays the same when the instance is replaced.
- The instance's user data: the bootstrap (next section), which installs Docker, the Compose plugin and Caddy and mounts the data volume.
- One security group: TCP 80 and 443 in from anywhere, TCP 80 and 443 out to anywhere, nothing else (no SSH, no app port). Opening a port means editing the list in `server/main.tf`, and the tests fail on it.
- One data volume for the database: encrypted gp3 with the AWS-managed key, 10 GiB by default (8 to 50), in the instance's zone (`us-east-1a` by default), attached with the instance stopped before any detach. It has `prevent_destroy`, so any plan that would delete or replace it fails.
- Outputs: `elastic_ip`, `hostname`, `instance_id` and `data_volume_id`. No secret.

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
   Expected: "Apply complete! Resources: 10 added, 0 changed, 0 destroyed." and the four outputs printed.
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

**Status: not yet run.** The rendered script passed `bash -n`, and `terraform test` (mock provider) checks its key lines, but it has never run on a host. Every "Expected" line below is what the author expects, not something seen, and every AWS or Ubuntu detail carries `VERIFY:` in the file until D19b.

### What it does

- **Packages:** `docker.io` and `docker-compose-v2` (the Compose plugin) from Ubuntu's own archive, signed by the Ubuntu archive key the image already trusts; `caddy` from Caddy's repository on Cloudsmith, whose downloaded keyring is accepted only when it holds exactly one primary key (its subkeys allowed) with the fingerprint `65760C51EDEA2017CEA2CA15155B6D79CA56EA34`; any other shape (no key, a second primary key, another fingerprint, a file `gpg` cannot read) stops the script before the keyring or the apt source is installed (`VERIFY:` the package names on noble, and that Cloudsmith serves this key). A package is installed only when missing; the bootstrap never upgrades one (Ubuntu's unattended upgrades patch them, `VERIFY:` that they are on in the image).
- **Docker:** log rotation of 10 MB per file and 3 files per container (`/etc/docker/daemon.json`); a weekly `docker image prune --all --filter until=168h` (`/etc/cron.d/socboard-docker-prune`), which removes only images no container uses, so never one a running container uses; Docker enabled at boot, and ordered after the data volume's mount by a drop-in (`/etc/systemd/system/docker.service.d/socboard.conf`, `RequiresMountsFor=/srv/socboard-data`), so it does not start without the volume. The drop-in is written after the fstab line and the mount, when the mount unit it names exists and is active, so the `daemon-reload` accepts it; the Docker the package install already started keeps running, and the order applies from the next boot. Containers come back after a reboot only if the Compose file gives them a restart policy (D11 or D20 sets it; this part assumes `restart: unless-stopped` on `db` and `app`).
- **The data volume:** found by its volume id (`/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol...`, `VERIFY:` the link name), never by a guessed device name. It is formatted as ext4 only when `blkid -p` finds no signature at all, so a volume with data is never formatted. On a replaced instance the volume already holds ext4, so `blkid -p` exits 0 and nothing is formatted; the fstab line is written again on the new root disk with the volume's unchanged UUID. It is mounted by file-system UUID with `nofail` and `x-systemd.device-timeout=30s` at `/srv/socboard-data`. A missing volume does not block the boot: Docker and Caddy stay stopped (each has `RequiresMountsFor=/srv/socboard-data`) while Systems Manager still reaches the host (`VERIFY:` at D19b). The bare directory under the mount point is made immutable, so nothing writes to the root disk while the volume is missing, and the script stops unless the volume is mounted. The script waits up to 10 minutes for the volume, which Terraform attaches after the instance starts.
- **The layout on the volume:** two siblings, created only after the volume is mounted. **`/srv/socboard-data/postgres`, the value `STACK_PGDATA_DIR` takes on the server** (root README, "Compose stack"; D20 sets it), owned by uid 999, the image's `postgres` user, mode 0700; Postgres writes its `pgdata` subdirectory there. `/srv/socboard-data/caddy`, Caddy's certificates and account, owned by the `caddy` user, mode 0700, outside anything a container mounts. Every run sets, each only when it differs: the mount root to `root:root` 0755 (#233's run left it owned by 999); `postgres/` to 999 and 0700 on its top level only (the database's files are never walked); `caddy/` and everything under it to the current `caddy` user, recursively, because the package creates that user again on a replaced instance and its uid may differ. Nothing on the volume is moved, copied or deleted: an existing `caddy/` stays where it is and is reused, and `postgres/` is created when missing. The script stops if the `caddy` user has uid 999.
- **Caddy:** `/etc/caddy/Caddyfile` serves the output `hostname` (the Elastic IP through sslip.io, for example `203-0-113-10.sslip.io`, Deploy decision 6; `VERIFY:` the name format) over HTTPS and proxies to `127.0.0.1:8000` only. The admin endpoint is off. The certificate comes from the ACME directory in the variable `acme_ca`: Let's Encrypt production by default, or its staging directory for testing. The account has no contact address (decision 6: a real address or none; `VERIFY:` that Let's Encrypt accepts that). Caddy's certificate and account live on the data volume (`/srv/socboard-data/caddy`), so a replaced instance keeps them, and Caddy does not start without the volume.
- **The forwarded address (for D20):** Caddy replaces any `X-Forwarded-For` the caller sent with the caller's address. The app sees the Compose network's gateway as the peer, `10.231.77.1` for the `stack` network in `compose.yaml`, whose gateway is pinned there (`gateway: 10.231.77.1`, #261). The pin is needed: without it, Docker 29.1.3 on the server gave the network the gateway `10.231.77.128`, the first address of the `ip_range` `10.231.77.128/25`, and with docker-proxy in use that gateway is the peer the app sees from host Caddy (the maintainer's measurement, 2026-11-16). `VERIFY:` measured 2026-11-16 (gateway behaviour); final value re-measured at #236 stage (b). D20 sets `SOCBOARD_TRUSTED_PROXIES=10.231.77.1`, sets `STACK_PGDATA_DIR=/srv/socboard-data/postgres`, leaves `FORWARDED_ALLOW_IPS` at its default, and does not start the compose `proxy` service: host Caddy is the one proxy.
- **Idempotent:** each step carries its own check (a package only when missing, a file only when its content differs, a format only of a blank volume, the fstab line only when the mount point has none, an owner or mode only when it differs, a restart only when a file changed), so a second run changes nothing. A failed file write stops the script with a message naming the file; no write failure is treated as "unchanged". Re-run by hand: `sudo bash /var/lib/cloud/instance/scripts/part-001` (`VERIFY:` the path).
- **Never held:** no secret in the script or the Caddy file, none written, none read (no Secrets Manager call). It does not deliver `compose.yaml`, the environment file or the deploy script (D20). The security group is unchanged: TCP 80 and 443 only, no port 22.

### Changed user data replaces the instance

`user_data_replace_on_change = true`. cloud-init runs user data once per instance, so without it a change would stop and start the same instance and never run the new script. With it, any change to the bootstrap or to `acme_ca` replaces the instance. The data volume and the Elastic IP are separate resources and survive: the old instance is stopped before the volume is detached (`stop_instance_before_detaching`), the new one mounts the same volume without formatting it, re-owns `caddy/` to its own `caddy` user, and the hostname stays. Caddy finds its certificate and ACME account for the same hostname in the kept storage and reuses them, with no new issuance (`VERIFY:` at D19b, by the certificate's serial number before and after). A replacement that does re-issue counts against Let's Encrypt's limits: 5 duplicate certificates for the same names in 7 days, and the weekly limit per registered domain, which every sslip.io user shares unless sslip.io is on the Public Suffix List (`VERIFY:` the current figures). Avoid repeated replacements in one week. What is lost is the root disk: D20's Compose file and environment file on it, so D20 deploys again after a replacement. A replacement also takes the newest Canonical image (the `ignore_changes` on `ami` applies only to an ordinary plan).

### Steps for the human in D19b (not yet run)

Run in `terraform/server/` as in the server section (same backend). These are for a server D19a (#207) already applied without the bootstrap; on a first apply with the bootstrap, follow the server section's steps instead.

1. `terraform plan`, and read it: `aws_instance.server` "must be replaced" because of `user_data`; `aws_volume_attachment.data` is replaced with it; `aws_eip_association.server` is new; the data volume and the Elastic IP are neither replaced nor destroyed.
   Expected: "Plan: 3 to add, 0 to change, 2 to destroy." (`VERIFY:` the Elastic IP may show as 1 to change while its association moves to the new resource). Stop if the data volume or the Elastic IP is to be destroyed.
2. For the first try, consider `terraform apply -var acme_ca=https://acme-staging-v02.api.letsencrypt.org/directory`, so a failure does not use up the production rate limit; then apply again without it (a second replacement).
   Expected: "Apply complete! Resources: 3 added, 0 changed, 2 destroyed." and the four outputs, including `hostname`.
3. In Systems Manager (Session Manager) on the new instance, read `/var/log/cloud-init-output.log`.
   Expected: it ends with `socboard-bootstrap: done`; `findmnt /srv/socboard-data` shows the volume by UUID; `stat -c %u /srv/socboard-data` prints `999`; `docker compose version` and `caddy version` print versions.
4. Run the bootstrap a second time (the re-run command above).
   Expected: it prints no `wrote` line and ends with `socboard-bootstrap: done`.
5. Reboot the instance from the console.
   Expected: the volume is mounted again and Caddy is running (`systemctl is-active caddy`). Then, after D20 has deployed, `https://<hostname>/api/health` answers from your browser with a valid certificate (a staging certificate shows as untrusted).

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
