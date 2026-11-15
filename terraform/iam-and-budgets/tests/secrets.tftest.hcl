# Offline checks of terraform/iam-and-budgets (issue #268). A mock AWS provider: no credential, no
# network to AWS, nothing created. Every run is a plan, and the assertions read the planned values, so
# they check the configuration's shape, not AWS behaviour (the VERIFY: items stay open until the apply).
# shortcut: a test can only read resources and outputs it names, so it cannot prove that no `data`
# source reads a secret; QA reads the diff for that (issue #268, the never-hold criterion).

mock_provider "aws" {}

variables {
  app_secret_arn      = "arn:aws:secretsmanager:us-east-1:123456789012:secret:socboard/app-AbCdEf"
  app_prod_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:socboard/app-prod-GhIjKl"
  monthly_budget_usd  = 25
  notification_email  = "maintainer@example.invalid"
}

run "instance_reads_exactly_the_two_secrets" {
  command = plan

  assert {
    condition     = length(jsondecode(aws_iam_role_policy.read_app_secret.policy).Statement) == 1
    error_message = "The inline policy must keep one statement."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.read_app_secret.policy).Statement[0].Action == ["secretsmanager:GetSecretValue"]
    error_message = "The one statement must allow exactly secretsmanager:GetSecretValue."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.read_app_secret.policy).Statement[0].Resource == ["arn:aws:secretsmanager:us-east-1:123456789012:secret:socboard/app-AbCdEf", "arn:aws:secretsmanager:us-east-1:123456789012:secret:socboard/app-prod-GhIjKl"]
    error_message = "The resources must be exactly dev's and prod's secret ARNs, in that order."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.read_app_secret.policy, "*")
    error_message = "The policy must hold no wildcard."
  }

  assert {
    condition     = jsondecode(aws_iam_role_policy.read_app_secret.policy).Statement[0].Effect == "Allow"
    error_message = "The one statement must be an Allow."
  }
}

run "output_is_still_only_the_profile_name" {
  command = plan

  assert {
    condition     = output.instance_profile_name == aws_iam_instance_profile.instance.name
    error_message = "instance_profile_name must stay the profile's name; no output carries a secret."
  }
}

run "wildcard_prod_arn_is_refused" {
  command = plan

  variables {
    app_prod_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:socboard/app-prod-*"
  }

  expect_failures = [var.app_prod_secret_arn]
}

run "star_name_prod_arn_is_refused" {
  command = plan

  variables {
    app_prod_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:*"
  }

  expect_failures = [var.app_prod_secret_arn]
}

run "other_region_prod_arn_is_refused" {
  command = plan

  variables {
    app_prod_secret_arn = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:socboard/app-prod-GhIjKl"
  }

  expect_failures = [var.app_prod_secret_arn]
}

run "dev_secret_as_prod_arn_is_refused" {
  command = plan

  variables {
    app_prod_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:socboard/app-AbCdEf"
  }

  expect_failures = [var.app_prod_secret_arn]
}

run "same_arn_twice_is_refused" {
  command = plan

  variables {
    app_secret_arn      = "arn:aws:secretsmanager:us-east-1:123456789012:secret:socboard/app-prod-GhIjKl"
    app_prod_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:socboard/app-prod-GhIjKl"
  }

  expect_failures = [var.app_prod_secret_arn]
}
