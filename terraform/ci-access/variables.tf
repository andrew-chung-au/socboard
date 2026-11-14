variable "github_owner" {
  description = "The GitHub account that owns the repository. Part of the trusted subject."
  type        = string
  default     = "andrew-chung-au"
}

variable "github_repo" {
  description = "The repository name. The trust policies name this repository only."
  type        = string
  default     = "socboard"
}

variable "github_owner_id" {
  description = "Numeric id of the owner account, in the OIDC subject. Not a secret. Read it with: gh api repos/<owner>/<repo> --jq .owner.id"
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id must be digits only."
  }
}

variable "github_repo_id" {
  description = "Numeric id of the repository, in the OIDC subject. Not a secret. It changes if the repository is deleted and recreated, and then no role can be assumed until it is updated. Read it with: gh api repos/<owner>/<repo> --jq .id"
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repo_id))
    error_message = "github_repo_id must be digits only."
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

variable "create_oidc_provider" {
  description = "Whether to create the GitHub OIDC provider. An AWS account holds only one provider per URL: set false and pass existing_oidc_provider_arn if the account already has one (the human checks, README step 1)."
  type        = bool
}

variable "existing_oidc_provider_arn" {
  description = "ARN of the account's existing GitHub OIDC provider. Used only when create_oidc_provider is false."
  type        = string
  default     = ""

  validation {
    condition     = var.create_oidc_provider || can(regex("^arn:aws:iam::[0-9]{12}:oidc-provider/token\\.actions\\.githubusercontent\\.com$", var.existing_oidc_provider_arn))
    error_message = "When create_oidc_provider is false, existing_oidc_provider_arn must be the ARN of the account's token.actions.githubusercontent.com provider."
  }
}

variable "apply_environment" {
  description = "Name of the GitHub Environment whose jobs may assume the apply role. The human limits it to main in GitHub (D19)."
  type        = string
  default     = "infra"
}

variable "deploy_environment" {
  description = "Name of the GitHub Environment whose jobs may assume the deploy role. The human limits it to main in GitHub (D19)."
  type        = string
  default     = "deploy"
}
