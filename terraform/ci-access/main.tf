# What CI may do in AWS (issue #156, Deploy item D16): one GitHub OIDC provider and three roles
# (plan, apply, deploy). Applied only by the human (Deploy decision record, decision 10), so CI
# cannot widen its own permissions. Nothing here was run: Terraform and AWS were not available when
# this was written, so every argument name and provider behaviour below is unchecked until the first
# `terraform init`, `validate` and apply (Deploy item D19). VERIFY: each one against the pinned provider.
#
# The provider lock file `.terraform.lock.hcl` is not in this folder yet: it cannot be made
# without `terraform init`. The human creates it at the first `init` and commits it (README).
#
# Never held here: no secret value and no access key. No data source or resource reads a secret.
# The only data source reads the caller's account number, to build role ARNs.

terraform {
  # One mention of the version besides terraform/.terraform-version (the single source): the same lower bound as terraform/state.
  required_version = ">= 1.11.0"

  required_providers {
    # VERIFY: the major version 6 names every argument used below. The lock file pins the exact one.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  # Named here on purpose: the region never comes from a default region setting
  # (Deploy decision record, part C). IAM is global, but the provider still needs one.
  # VERIFY: `region` is the provider argument name.
  region = "us-east-1"
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # The start of the token's `sub` claim, before the part that names the kind of run. For a
  # repository created after mid-2026 GitHub puts the immutable owner and repository ids in it, so a
  # renamed or recreated repository cannot match. On 2026-11-14 the GitHub API reported
  # `use_immutable_subject: true` for this repository and a prefix in exactly this shape.
  # VERIFY: the first real token's `sub` matches (D19). If it has no ids, the trust below fails
  # closed (nobody can assume a role); the fix is the README's "If the subject has no ids" note.
  sub_prefix = "repo:${var.repo_owner}@${var.repo_owner_id}/${var.repo_name}@${var.repo_id}"

  # The ARN of whichever provider applies: the reused one if the variable is set, else the one made
  # below (`one` is null when no provider was made). VERIFY: `coalesce` skips the null.
  oidc_provider_arn = coalesce(var.reuse_oidc_provider_arn, one(aws_iam_openid_connect_provider.github[*].arn))

  # Names shared with other Terraform parts. The instance role and profile are made by
  # terraform/iam-and-budgets (#162); the state bucket by terraform/state (#154).
  instance_role_arn = "arn:aws:iam::${local.account_id}:role/socboard-instance"
  deploy_doc_name   = "socboard-deploy"
  deploy_doc_arn    = "arn:aws:ssm:us-east-1:${local.account_id}:document/${local.deploy_doc_name}"

  # CI's rights over the state bucket stop at this prefix: the CI-applied server part (D17b) keeps its
  # state under it, so CI cannot touch the state of the human-applied parts. D17b's backend `key` must
  # start with it.
  server_state_prefix = "server/"
  state_bucket_arn    = "arn:aws:s3:::${var.state_bucket_name}"

  # Reading a secret's value is denied to all three roles (only the instance reads the secret).
  # The application secret lives in Secrets Manager (Deploy decision 5), so these two are the
  # actions that return a value. Parameter Store reads are not denied: D17b may look up a public
  # image id there. VERIFY: both action names.
  secret_value_actions = [
    "secretsmanager:GetSecretValue",
    "secretsmanager:BatchGetSecretValue",
  ]

  oidc_audience = "sts.amazonaws.com"
}

# One provider for GitHub Actions. VERIFY: `thumbprint_list` may be omitted in provider 6.x (AWS
# validates GitHub's certificate against its own trusted authorities); if `validate` or the apply
# asks for one, take it from the AWS documentation, not from this repository.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.reuse_oidc_provider_arn == null ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = [local.oidc_audience]

  tags = {
    project = "socboard"
  }
}
