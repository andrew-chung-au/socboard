# The role ARNs, which the workflows (D18b, D22a, D22b) read as repository secrets: each ARN
# contains the account number, and Actions logs mask secrets but not variables. No output here
# holds a secret value, so none is marked sensitive.
output "plan_role_arn" {
  description = "Role for pull request plan runs."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "Role for the apply job in the infra Environment."
  value       = aws_iam_role.apply.arn
}

output "deploy_role_arn" {
  description = "Role for the deploy jobs in the deploy and deploy-prod Environments."
  value       = aws_iam_role.deploy.arn
}
