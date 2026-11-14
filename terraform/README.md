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
4. Commit `.terraform.lock.hcl`. It cannot be made without `init`, so it is not in the repository yet and is tracked once it exists.
   Expected: `git status` lists `terraform/state/.terraform.lock.hcl` as new.
5. Copy `backend.tf.example` to `backend.tf`, then move the state: `terraform init -migrate-state -backend-config="bucket=<your bucket_name>"` and answer `yes`.
   Expected: `init` asks whether to copy the existing local state to the new backend; after `yes` it prints "Successfully configured the backend "s3"!" and `terraform plan` reports "No changes."
6. Check the move: `terraform state list` lists the six resources, and the S3 console shows `state/terraform.tfstate` in the bucket. Then delete the local `terraform.tfstate` and `terraform.tfstate.backup`.
   Expected: the list shows `aws_s3_bucket.state` and five more; during `plan` a `state/terraform.tfstate.tflock` object appears briefly (native lock file).

Later `init` runs need the same `-backend-config="bucket=<name>"` argument.

## Teardown (the human's step, destructive)

The bucket has `force_destroy = false` and holds every version of the state, so Terraform will not delete it while it has objects. The human must empty it of **all object versions and delete markers** (a plain delete of the current objects leaves the old versions) before `terraform destroy` can delete it. An agent never does this. The bucket is the last resource to go (Deploy item D23): destroy everything else first, because it holds their state.

## What is deliberately not here

No outputs, no secrets, no KMS key. The bucket name is a variable so that no AWS account number enters the repository. Noncurrent versions expire after 90 days (`state/main.tf`).
