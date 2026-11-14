variable "repo_owner" {
  description = "Login of the GitHub account that owns this repository. Every trust policy names it."
  type        = string
  default     = "andrew-chung-au"
}

variable "repo_name" {
  description = "Name of this repository. Every trust policy names it, and no other repository can match."
  type        = string
  default     = "socboard"
}

variable "repo_owner_id" {
  description = "Numeric id of the owner account, as it appears inside the OIDC subject. Not a secret. Read it with: gh api repos/<owner>/<repo> --jq .owner.id"
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.repo_owner_id))
    error_message = "repo_owner_id must be digits only."
  }
}

variable "repo_id" {
  description = "Numeric id of this repository, as it appears inside the OIDC subject. Not a secret. Deleting and recreating the repository gives it a new id, and until this value is updated no role can be assumed. Read it with: gh api repos/<owner>/<repo> --jq .id"
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.repo_id))
    error_message = "repo_id must be digits only."
  }
}

variable "state_bucket_name" {
  description = "Name of the state bucket made by terraform/state. The plan and apply roles are limited to the server state under it."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be 3 to 63 characters: lowercase letters, digits, dots and hyphens, starting and ending with a letter or digit."
  }
}

variable "reuse_oidc_provider_arn" {
  description = "Null makes this part register GitHub as an identity provider. AWS allows one registration per URL in an account, so when GitHub is already registered, set the existing registration's ARN here and nothing is registered again (the human checks first, README step 1)."
  type        = string
  default     = null

  validation {
    condition     = var.reuse_oidc_provider_arn == null || can(regex("^arn:aws:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.reuse_oidc_provider_arn))
    error_message = "reuse_oidc_provider_arn must be null or the full ARN of the token.actions.githubusercontent.com provider already registered in this AWS account."
  }
}

variable "apply_environment" {
  description = "Name of the GitHub Environment whose jobs may assume the apply role. The human restricts it to main in GitHub (D19)."
  type        = string
  default     = "infra"
}

variable "deploy_environment" {
  description = "Name of the GitHub Environment whose jobs may assume the deploy role. The human restricts it to main in GitHub (D19)."
  type        = string
  default     = "deploy"
}
