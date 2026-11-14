# The apply role: creates and changes the CI-applied server part (D17b) and nothing in IAM.
# It is assumed only by a job in the `infra` GitHub Environment. The token's subject carries the
# Environment name, not the branch, so the branch limit (main only) is the Environment's own
# setting, made by the human in D19 and checked there (the human task file named in the README's CI access section).

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

locals {
  ec2_arn         = "arn:aws:ec2:us-east-1:${local.account_id}"
  request_tagged  = { StringEquals = { "aws:RequestTag/project" = "socboard" } }
  resource_tagged = { StringEquals = { "aws:ResourceTag/project" = "socboard" } }
}

# VERIFY: that the list covers D17b (instance, data volume, Elastic IP, security group, the deploy
# document). It starts small on purpose: if the first apply is denied, the human adds the one action
# here and applies this part by hand. Action names, resource types and condition keys were checked
# against AWS's service reference JSON (servicereference.us-east-1.amazonaws.com) on 2026-11-14.
#
# EC2 writes are tag-scoped: what the role creates must carry `project=socboard` in the create call,
# and what it changes must already carry it. D17b must therefore tag the instance, its volumes
# (`volume_tags`), the security group and the Elastic IP in the create call. AWS cannot tag-scope:
# `ec2:Describe*` (reads, no resource); the image, subnet and network interface a launch refers to;
# the VPC a security group is made in; a new security group rule; and the network interface named
# when an Elastic IP is moved. Each of those calls also names a tagged resource, which is checked.
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
        Sid       = "ReadEc2"
        Effect    = "Allow"
        Action    = ["ec2:Describe*"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = "us-east-1" } }
      },
      # A new instance and its volumes must be tagged in the launch call. VERIFY: that Terraform sends
      # the volume tags in the launch call (`volume_tags`), not afterwards.
      {
        Sid       = "LaunchOnlyTaggedInstances"
        Effect    = "Allow"
        Action    = ["ec2:RunInstances"]
        Resource  = ["${local.ec2_arn}:instance/*", "${local.ec2_arn}:volume/*"]
        Condition = local.request_tagged
      },
      # A launch may only use a security group this role made (so already tagged).
      {
        Sid       = "LaunchIntoTaggedSecurityGroups"
        Effect    = "Allow"
        Action    = ["ec2:RunInstances"]
        Resource  = ["${local.ec2_arn}:security-group/*"]
        Condition = local.resource_tagged
      },
      # What a launch only refers to, untagged: the public image, the default subnet and the
      # instance's own network interface.
      {
        Sid    = "LaunchFromSharedResources"
        Effect = "Allow"
        Action = ["ec2:RunInstances"]
        Resource = [
          "arn:aws:ec2:us-east-1::image/*",
          "${local.ec2_arn}:subnet/*",
          "${local.ec2_arn}:network-interface/*",
        ]
      },
      {
        Sid    = "CreateOnlyTaggedResources"
        Effect = "Allow"
        Action = ["ec2:CreateVolume", "ec2:CreateSecurityGroup", "ec2:AllocateAddress"]
        Resource = [
          "${local.ec2_arn}:volume/*",
          "${local.ec2_arn}:security-group/*",
          "${local.ec2_arn}:elastic-ip/*",
        ]
        Condition = local.request_tagged
      },
      # The default VPC carries no tag of this project.
      {
        Sid      = "CreateSecurityGroupInAnyVpc"
        Effect   = "Allow"
        Action   = ["ec2:CreateSecurityGroup"]
        Resource = ["${local.ec2_arn}:vpc/*"]
      },
      # Tags can be added only by one of the create calls above (whose own tags are checked), or to a
      # resource that already carries `project=socboard`. So this role cannot tag another instance
      # into the set the deploy role may reach.
      {
        Sid      = "TagOnlyWhileCreating"
        Effect   = "Allow"
        Action   = ["ec2:CreateTags"]
        Resource = ["${local.ec2_arn}:instance/*", "${local.ec2_arn}:volume/*", "${local.ec2_arn}:security-group/*", "${local.ec2_arn}:elastic-ip/*"]
        Condition = {
          StringEquals = {
            "ec2:CreateAction" = ["RunInstances", "CreateVolume", "CreateSecurityGroup", "AllocateAddress"]
          }
        }
      },
      # Every change to an existing resource needs the resource to carry `project=socboard`.
      # The instance profile actions attach a profile; which role it holds is limited by PassRole below.
      {
        Sid    = "ChangeOnlyTaggedResources"
        Effect = "Allow"
        Action = [
          "ec2:TerminateInstances",
          "ec2:StartInstances",
          "ec2:StopInstances",
          "ec2:ModifyInstanceAttribute",
          "ec2:ModifyInstanceMetadataOptions",
          "ec2:DeleteVolume",
          "ec2:AttachVolume",
          "ec2:DetachVolume",
          "ec2:ModifyVolume",
          "ec2:ReleaseAddress",
          "ec2:AssociateAddress",
          "ec2:DisassociateAddress",
          "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:ModifySecurityGroupRules",
          "ec2:CreateTags",
          "ec2:DeleteTags",
          "ec2:AssociateIamInstanceProfile",
          "ec2:ReplaceIamInstanceProfileAssociation",
          "ec2:DisassociateIamInstanceProfile",
        ]
        Resource = [
          "${local.ec2_arn}:instance/*",
          "${local.ec2_arn}:volume/*",
          "${local.ec2_arn}:security-group/*",
          "${local.ec2_arn}:elastic-ip/*",
        ]
        Condition = local.resource_tagged
      },
      # Sub-resources AWS also checks in those calls but that carry no tag: a new security group rule,
      # and the instance's network interface when the Elastic IP is moved. The tagged security group or
      # Elastic IP in the same call is checked by the statement above.
      {
        Sid    = "ChangeUntaggedSubResources"
        Effect = "Allow"
        Action = [
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:ModifySecurityGroupRules",
          "ec2:AssociateAddress",
          "ec2:DisassociateAddress",
        ]
        Resource = ["${local.ec2_arn}:security-group-rule/*", "${local.ec2_arn}:network-interface/*"]
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
      # Every IAM action except that one PassRole is denied, by two statements. A plain `Deny iam:*`
      # would also deny the PassRole above, because an explicit deny always wins. Together they need no
      # list of write verbs, so a new IAM action is denied too:
      # 1. every IAM action, reads included, on every resource but the instance role, including this
      #    role, its policy, any snapshot role (human-applied, #162) and actions on `*`;
      # 2. every action but PassRole on the instance role itself, so it cannot be changed either.
      # IAM reads are denied as well: D17b refers to the instance profile by name and uses no IAM data
      # source, and launching with a profile needs only this PassRole. If D17b ever needs an IAM read,
      # the human adds it here as a narrow exception. `iam:*` also covers service-linked roles: if D17b
      # needs one, the human creates it.
      {
        Sid         = "DenyIamOnEveryOtherResource"
        Effect      = "Deny"
        Action      = ["iam:*"]
        NotResource = [local.instance_role_arn]
      },
      {
        Sid       = "DenyAllButPassOnTheInstanceRole"
        Effect    = "Deny"
        NotAction = ["iam:PassRole"]
        Resource  = [local.instance_role_arn]
      },
      # The budgets and the snapshot schedule are human-applied with the IAM part (Deploy decision 10).
      {
        Sid      = "DenyHumanAppliedServices"
        Effect   = "Deny"
        Action   = local.human_only_actions
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
