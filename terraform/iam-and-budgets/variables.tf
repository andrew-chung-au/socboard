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

variable "app_prod_secret_arn" {
  description = "ARN of the Secrets Manager secret `socboard/app-prod`, which the instance may also read (issue #268). The human creates the secret, empty, outside Terraform; only its ARN is named here, never its value."
  type        = string

  # Secrets Manager adds a dash and six random characters to the name in the ARN. The pattern names
  # the one secret, so a wildcard, another region and another secret (including `socboard/app`) fail.
  validation {
    condition     = can(regex("^arn:aws:secretsmanager:us-east-1:[0-9]{12}:secret:socboard/app-prod-[A-Za-z0-9]{6}$", var.app_prod_secret_arn))
    error_message = "app_prod_secret_arn must be the full ARN of the secret socboard/app-prod in us-east-1 (name, dash, six characters), with no wildcard."
  }

  validation {
    condition     = var.app_prod_secret_arn != var.app_secret_arn
    error_message = "app_prod_secret_arn must differ from app_secret_arn."
  }
}
