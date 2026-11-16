# Offline checks of terraform/ci-access (issue #268). A mock AWS provider: no credential, no network
# to AWS, nothing created. Every run is a plan, and the assertions read the planned values, so they
# check the configuration's shape, not AWS behaviour (the VERIFY: items stay open until the human's apply).
# shortcut: a test can only read resources and outputs it names, so it cannot prove that no `data`
# source reads a secret; QA reads the diff for that (issue #268, the never-hold criterion).

mock_provider "aws" {}

# A known account number, so the role ARNs and the provider ARN are known at plan time.
override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "123456789012"
  }
}

variables {
  repo_owner_id           = "111"
  repo_id                 = "222"
  state_bucket_name       = "example-state-bucket"
  reuse_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
}

run "deploy_trust_names_exactly_two_subjects" {
  command = plan

  assert {
    condition     = length(jsondecode(aws_iam_role.deploy.assume_role_policy).Statement) == 1
    error_message = "The deploy role's trust must keep one statement."
  }

  assert {
    condition     = [for k, v in jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition : k] == ["StringEquals"]
    error_message = "The deploy trust's only condition operator must be StringEquals (no StringLike, no ForAnyValue)."
  }

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "The deploy trust must require the audience sts.amazonaws.com."
  }

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == ["repo:andrew-chung-au@111/socboard@222:environment:deploy", "repo:andrew-chung-au@111/socboard@222:environment:deploy-prod"]
    error_message = "The deploy trust's subject must be exactly the deploy and deploy-prod subjects, with the numeric ids."
  }

  assert {
    condition     = length(jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"]) == 2
    error_message = "The deploy trust's subject list must have length 2."
  }

  assert {
    condition     = !strcontains(aws_iam_role.deploy.assume_role_policy, "*") && !strcontains(aws_iam_role.deploy.assume_role_policy, "?")
    error_message = "The deploy trust must hold no wildcard."
  }
}

run "apply_and_plan_trusts_keep_one_subject" {
  command = plan

  assert {
    condition     = jsondecode(aws_iam_role.apply.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:andrew-chung-au@111/socboard@222:environment:infra"
    error_message = "The apply role must still trust only the infra Environment's subject."
  }

  assert {
    condition     = jsondecode(aws_iam_role.plan.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:andrew-chung-au@111/socboard@222:pull_request"
    error_message = "The plan role must still trust only the pull request subject."
  }
}

run "deploy_policy_keeps_its_denies_and_no_new_statement" {
  command = plan

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.deploy.policy).Statement) == 6
    error_message = "The deploy role's permission policy must keep its six statements."
  }

  assert {
    condition     = length([for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement : s.Sid if s.Effect == "Deny"]) == 3
    error_message = "The deploy role must keep its three explicit denies."
  }
}

run "plan_policy_reads_the_deploy_document" {
  command = plan

  assert {
    condition     = length([for s in jsondecode(aws_iam_role_policy.plan.policy).Statement : s if s.Sid == "ReadDeployDocument"]) == 1
    error_message = "The plan policy must hold exactly one ReadDeployDocument statement."
  }

  assert {
    condition     = [for s in jsondecode(aws_iam_role_policy.plan.policy).Statement : s.Effect if s.Sid == "ReadDeployDocument"] == ["Allow"]
    error_message = "The ReadDeployDocument statement must be an Allow."
  }

  assert {
    condition     = [for s in jsondecode(aws_iam_role_policy.plan.policy).Statement : sort(s.Action) if s.Sid == "ReadDeployDocument"] == [["ssm:DescribeDocument", "ssm:DescribeDocumentPermission", "ssm:GetDocument", "ssm:ListTagsForResource"]]
    error_message = "The ReadDeployDocument statement must allow exactly the four read actions, including ssm:DescribeDocumentPermission (#209)."
  }

  assert {
    condition     = [for s in jsondecode(aws_iam_role_policy.plan.policy).Statement : length(s.Action) if s.Sid == "ReadDeployDocument"] == [4]
    error_message = "The ReadDeployDocument statement's action list must have length 4."
  }

  assert {
    condition     = [for s in jsondecode(aws_iam_role_policy.plan.policy).Statement : s.Resource if s.Sid == "ReadDeployDocument"] == [["arn:aws:ssm:us-east-1:123456789012:document/socboard-deploy"]]
    error_message = "The ReadDeployDocument statement's resource must be exactly the socboard-deploy document ARN."
  }
}

run "wildcard_environment_is_refused" {
  command = plan

  variables {
    deploy_prod_environment = "*"
  }

  expect_failures = [var.deploy_prod_environment]
}

run "prefix_wildcard_environment_is_refused" {
  command = plan

  variables {
    deploy_prod_environment = "deploy*"
  }

  expect_failures = [var.deploy_prod_environment]
}

run "existing_environment_name_is_refused" {
  command = plan

  variables {
    deploy_prod_environment = "deploy"
  }

  expect_failures = [var.deploy_prod_environment]
}

run "apply_environment_name_is_refused" {
  command = plan

  variables {
    deploy_prod_environment = "infra"
  }

  expect_failures = [var.deploy_prod_environment]
}
