# The deploy role: runs one Systems Manager document on the tagged server and reads the result.
# The document (created by the CI-applied server part) is a fixed script with one parameter, the
# image digest, checked by a pattern in the document itself. This role has no generic shell
# document, so it cannot run an arbitrary command on the host. It is assumed only by a job in the
# `deploy` GitHub Environment; the branch limit is that Environment's own setting (D19).

resource "aws_iam_role" "deploy" {
  name = "socboard-ci-deploy"

  # VERIFY: the `sub` value for a job that names an Environment is `<prefix>:environment:<name>`.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = local.oidc_audience
          "token.actions.githubusercontent.com:sub" = "${local.sub_prefix}:environment:${var.deploy_environment}"
        }
      }
    }]
  })

  tags = {
    project = "socboard"
  }
}

resource "aws_iam_role_policy" "deploy" {
  name = "deploy"
  role = aws_iam_role.deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # One call to `SendCommand` is checked against the instance and, separately, the document.
      # Only the instance carries the `project` tag, so each gets its own statement; a single
      # tag-conditioned statement would fail on the document. The service reference lists `SendCommand`
      # resources as bucket, document, instance and managed-instance. VERIFY: this split at D19.
      {
        Sid      = "SendToTaggedInstances"
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = ["arn:aws:ec2:us-east-1:${local.account_id}:instance/*"]
        Condition = {
          StringEquals = { "ssm:resourceTag/project" = "socboard" }
        }
      },
      {
        Sid      = "SendTheDeployDocument"
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = [local.deploy_doc_arn]
      },
      # AWS authorises `GetCommandInvocation` only on `*`, with no condition key (service reference
      # JSON, 2026-11-14), so it cannot be limited to the deploy document or the tagged instance. It
      # needs a command id, which the workflow has from its own `SendCommand`. `ListCommands` and
      # `ListCommandInvocations` (also `*` only) are left out so the role cannot list other commands.
      {
        Sid      = "ReadTheCommandResult"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation"]
        Resource = "*"
      },
      # Backs up the allow list: a document owned by AWS has no account number in its ARN, so this
      # pattern covers every AWS-owned document and no document of this account. VERIFY.
      {
        Sid      = "DenyAwsOwnedDocuments"
        Effect   = "Deny"
        Action   = ["ssm:SendCommand"]
        Resource = ["arn:aws:ssm:*::document/*"]
      },
      # Sending the document passes no role, so no IAM action is needed at all.
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
