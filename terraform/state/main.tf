# The Terraform state bucket (issue #154). Applied only by the human (Deploy decisions).
# Nothing here was run: Terraform and AWS were not available when this was written, so every
# argument name and provider behaviour below is unchecked until the first `terraform init`,
# `validate` and apply (Deploy item D19). VERIFY: each one against the pinned provider.
#
# The provider lock file `.terraform.lock.hcl` is not in this folder yet: it cannot be made
# without `terraform init`. The human creates it at the first `init` and commits it.

terraform {
  # One mention of the version besides terraform/.terraform-version (the single source).
  # VERIFY: 1.11 is the first release where `use_lockfile` (S3 native locking) is not experimental.
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
  # (Deploy decision record, part C). VERIFY: `region` is the provider argument name.
  region = "us-east-1"
}

resource "aws_s3_bucket" "state" {
  bucket = var.bucket_name

  # Never let Terraform delete a bucket that holds versions. The human empties it first (README).
  force_destroy = false

  tags = {
    project = "socboard"
  }
}

# VERIFY: this resource and its four arguments exist in provider 6.x.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# VERIFY: `versioning_configuration { status = "Enabled" }`.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

# VERIFY: `rule { apply_server_side_encryption_by_default { sse_algorithm } }`.
# AES256 is S3-managed encryption: no KMS key to create, so no extra IAM or key policy.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# VERIFY: `noncurrent_version_expiration { noncurrent_days }` and that an empty `filter {}`
# (the whole bucket) is accepted without a warning.
# Old versions of the state file are kept 90 days, then expire, so the bucket does not grow without bound.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }

  # The provider documents this ordering for versioned buckets. VERIFY.
  depends_on = [aws_s3_bucket_versioning.state]
}

# Refuses every request that does not use TLS, for the bucket and its objects.
# VERIFY: the policy JSON (`aws:SecureTransport`) and that `bucket_policy` applies after the public access block.
resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyNonTLS"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.state.arn,
        "${aws_s3_bucket.state.arn}/*",
      ]
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.state]
}
