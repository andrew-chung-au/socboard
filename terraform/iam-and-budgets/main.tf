# The instance's IAM role, the daily snapshot schedule and the budgets (issue #162, Deploy item D17a).
# Applied only by the human (Deploy decision record, decision 10). Nothing here was run:
# Terraform and AWS were not available when this was written, so every argument name and
# provider behaviour below is unchecked until the first `terraform init`, `validate` and apply
# (Deploy item D19). VERIFY: each one against the pinned provider.
#
# The provider lock file `.terraform.lock.hcl` is not in this folder yet: it cannot be made
# without `terraform init`. The human creates it at the first `init` and commits it (README).
#
# Never held here: no secret value. The secret's ARN is a variable; no data source reads the secret.

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
  # (Deploy decision record, part C). IAM is global and budgets are account-wide, but the provider still needs one.
  # VERIFY: `region` is the provider argument name.
  region = "us-east-1"
}

# ---- Instance role and profile ----

# Trust: only the EC2 service may assume the role. VERIFY: `assume_role_policy` takes this JSON.
resource "aws_iam_role" "instance" {
  name = "socboard-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    project = "socboard"
  }
}

# The agent's permissions, from the AWS-managed policy (access is through Systems Manager only).
# VERIFY: this managed policy name and ARN, and that it is no wider than the agent needs (check its document at the first apply).
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# The one extra permission: read one named secret. No other action, no other resource, no write.
# VERIFY: `aws_iam_role_policy` arguments `name`, `role`, `policy`.
resource "aws_iam_role_policy" "read_app_secret" {
  name = "read-app-secret"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadOneSecret"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = [var.app_secret_arn]
    }]
  })
}

# VERIFY: `aws_iam_instance_profile` with `name` and `role`; D17b attaches it by name.
resource "aws_iam_instance_profile" "instance" {
  name = "socboard-instance"
  role = aws_iam_role.instance.name

  tags = {
    project = "socboard"
  }
}

# ---- Daily snapshots of the data volume ----
#
# Smallest service that works: Amazon Data Lifecycle Manager (DLM). It takes snapshots on a schedule of every
# volume that carries a tag, so the volume need not exist yet, and expires them by count. AWS Backup would add a
# vault and a plan for no gain here.

# VERIFY: whether DLM needs this role. `aws_dlm_lifecycle_policy` takes `execution_role_arn`; the trust
# principal is dlm.amazonaws.com; the managed policy name and ARN below.
resource "aws_iam_role" "snapshot" {
  name = "socboard-snapshot"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "dlm.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    project = "socboard"
  }
}

# Used only to take, copy and delete snapshots of tagged volumes.
resource "aws_iam_role_policy_attachment" "snapshot" {
  role       = aws_iam_role.snapshot.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

# Retention: the 7 newest daily snapshots are kept, older ones are deleted by DLM.
# The tag must be unique to this deployment: every volume carrying project=socboard is snapshotted.
# VERIFY: the `policy_details`, `schedule`, `create_rule` (`interval`, `interval_unit`, `times`),
# `retain_rule` (`count`) and `target_tags` names, and that `times` is in UTC. The provider rejects
# a description with a comma (CI found it); allowed: letters, digits, spaces, underscore, hyphen.
resource "aws_dlm_lifecycle_policy" "data_volume" {
  description        = "Daily snapshots of the socboard data volume - 7 kept"
  execution_role_arn = aws_iam_role.snapshot.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { project = "socboard" }

    schedule {
      name      = "daily"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["03:00"]
      }

      retain_rule {
        count = 7
      }
    }
  }

  tags = {
    project = "socboard"
  }

  depends_on = [aws_iam_role_policy_attachment.snapshot]
}

# ---- Budget ----

# Three notifications on one monthly cost budget: half, 80 percent and the full limit of actual spend.
# VERIFY: the budgets API is served from us-east-1 (the provider region above), the argument names
# (`limit_amount` is a string, `limit_unit`, `time_unit`, `notification` with `comparison_operator`,
# `threshold`, `threshold_type`, `notification_type`, `subscriber_email_addresses`), that `tags` is
# accepted, and that the address gets a confirmation mail or none.
resource "aws_budgets_budget" "monthly" {
  name         = "socboard-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 80, 100]

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.notification_email]
    }
  }

  tags = {
    project = "socboard"
  }
}
