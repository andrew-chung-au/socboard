# The plan role: read-only, for pull request runs. Any pull request in this repository can assume
# it (the subject names the event, not the branch), so it holds nothing that can change a resource
# or read a secret.

resource "aws_iam_role" "plan" {
  name = "socboard-ci-plan"

  # VERIFY: the `sub` value for a pull request run is `<prefix>:pull_request`.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = local.oidc_audience
          "token.actions.githubusercontent.com:sub" = "${local.sub_prefix}:pull_request"
        }
      }
    }]
  })

  tags = {
    project = "socboard"
  }
}

# VERIFY: the actions a `plan` of the server part needs. The list starts small; if the first plan is
# denied a read, the human adds that one read here and applies it.
# A read-only role cannot write the native lock file, so CI's plan runs with `-lock=false` (D18b).
resource "aws_iam_role_policy" "plan" {
  name = "plan"
  role = aws_iam_role.plan.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadServerState"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.state_bucket_arn}/${local.server_state_prefix}*"]
      },
      {
        Sid      = "ListStateBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.state_bucket_arn]
      },
      {
        Sid      = "ReadServerResources"
        Effect   = "Allow"
        Action   = ["ec2:Describe*"]
        Resource = "*"
      },
      {
        Sid      = "ReadDeployDocument"
        Effect   = "Allow"
        Action   = ["ssm:DescribeDocument", "ssm:GetDocument", "ssm:ListTagsForResource"]
        Resource = [local.deploy_doc_arn]
      },
      # A plan of the server part reads no IAM, budget or snapshot schedule.
      {
        Sid      = "DenyHumanAppliedServices"
        Effect   = "Deny"
        Action   = concat(["iam:*"], local.human_only_actions)
        Resource = "*"
      },
      {
        Sid      = "DenySecretValues"
        Effect   = "Deny"
        Action   = local.secret_value_actions
        Resource = "*"
      },
    ]
  })
}
