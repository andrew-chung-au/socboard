# The apply role: creates and changes the CI-applied server part (D17b) and nothing in IAM.
# It is assumed only by a job in the `infra` GitHub Environment. The token's subject carries the
# Environment name, not the branch, so the branch limit (main only) is the Environment's own
# setting, made by the human in D19 and checked there (README, "Check that a non-main branch is refused").

resource "aws_iam_role" "apply" {
  name = "socboard-ci-apply"

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
          "token.actions.githubusercontent.com:sub" = "${local.sub_prefix}:environment:${var.apply_environment}"
        }
      }
    }]
  })

  tags = {
    project = "socboard"
  }
}

# VERIFY: every action name below, and that the list covers D17b (instance, data volume, Elastic IP,
# security group, the deploy document). It starts small on purpose: if the first apply is denied,
# the human adds the one action here and applies this part by hand.
resource "aws_iam_role_policy" "apply" {
  name = "apply"
  role = aws_iam_role.apply.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # The resource pattern includes the native lock file next to the state object.
      {
        Sid      = "ServerStateReadWrite"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = ["${local.state_bucket_arn}/${local.server_state_prefix}*"]
      },
      {
        Sid      = "ListStateBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.state_bucket_arn]
      },
      {
        Sid    = "ManageServerCompute"
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "ec2:RunInstances",
          "ec2:TerminateInstances",
          "ec2:StartInstances",
          "ec2:StopInstances",
          "ec2:ModifyInstanceAttribute",
          "ec2:ModifyInstanceMetadataOptions",
          "ec2:CreateVolume",
          "ec2:DeleteVolume",
          "ec2:AttachVolume",
          "ec2:DetachVolume",
          "ec2:ModifyVolume",
          "ec2:AllocateAddress",
          "ec2:ReleaseAddress",
          "ec2:AssociateAddress",
          "ec2:DisassociateAddress",
          "ec2:CreateSecurityGroup",
          "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:ModifySecurityGroupRules",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          # Attaching the instance profile. Which profile is limited by the PassRole rule below:
          # the instance role is the only role this role may pass. VERIFY: these three names.
          "ec2:AssociateIamInstanceProfile",
          "ec2:ReplaceIamInstanceProfileAssociation",
          "ec2:DisassociateIamInstanceProfile",
        ]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:RequestedRegion" = "us-east-1" }
        }
      },
      {
        Sid    = "ManageDeployDocument"
        Effect = "Allow"
        Action = [
          "ssm:CreateDocument",
          "ssm:UpdateDocument",
          "ssm:UpdateDocumentDefaultVersion",
          "ssm:DeleteDocument",
          "ssm:DescribeDocument",
          "ssm:GetDocument",
          "ssm:AddTagsToResource",
          "ssm:RemoveTagsFromResource",
          "ssm:ListTagsForResource",
        ]
        Resource = [local.deploy_doc_arn]
      },
      # The one role this role may hand to a service: the instance role, to EC2 only.
      # VERIFY: that RunInstances and the profile actions are authorised by this permission.
      {
        Sid      = "PassOnlyTheInstanceRole"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [local.instance_role_arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ec2.amazonaws.com" }
        }
      },
      # No other role may be passed or attached, including any snapshot role (human-applied, #162).
      {
        Sid         = "DenyPassingAnyOtherRole"
        Effect      = "Deny"
        Action      = ["iam:PassRole"]
        NotResource = [local.instance_role_arn]
      },
      # Every IAM write, on every resource, including this role and its own policy.
      # Verbs, not `iam:*`, so the reads stay possible. VERIFY: that no other write verb exists
      # (check the IAM action list at the first apply). `iam:Create*` includes service-linked roles:
      # if D17b needs one, the human creates it.
      {
        Sid    = "DenyEveryIamWrite"
        Effect = "Deny"
        Action = [
          "iam:Add*",
          "iam:Attach*",
          "iam:Change*",
          "iam:Create*",
          "iam:Deactivate*",
          "iam:Delete*",
          "iam:Detach*",
          "iam:Enable*",
          "iam:Put*",
          "iam:Remove*",
          "iam:Reset*",
          "iam:Resync*",
          "iam:Set*",
          "iam:Tag*",
          "iam:Untag*",
          "iam:Update*",
          "iam:Upload*",
        ]
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
