variable "app_secret_arn" {
  description = "ARN of the one Secrets Manager secret the instance may read. The human creates the secret outside Terraform; only its ARN is named here, never its value."
  type        = string

  validation {
    condition     = can(regex("^arn:aws:secretsmanager:us-east-1:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+$", var.app_secret_arn))
    error_message = "app_secret_arn must be the full ARN of one Secrets Manager secret in us-east-1, with no wildcard."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost limit in US dollars. The notifications fire at half, 80 percent and the full amount."
  type        = number

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be greater than 0."
  }
}

variable "notification_email" {
  description = "Address that receives the budget notifications. Set it in the git-ignored variable file."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+$", var.notification_email))
    error_message = "notification_email must look like an email address."
  }
}
