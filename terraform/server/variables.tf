# Cost limits. Every variable has a committed default, so CI can plan and apply with no variable
# file; none holds a secret or an account number.

variable "instance_type" {
  description = "EC2 instance type. Only small T3 sizes are allowed, to cap the cost."
  type        = string
  default     = "t3.small"

  validation {
    condition     = contains(["t3.micro", "t3.small", "t3.medium"], var.instance_type)
    error_message = "instance_type must be one of t3.micro, t3.small or t3.medium."
  }
}

variable "data_volume_gib" {
  description = "Size of the data volume in GiB. It can grow later, never shrink."
  type        = number
  default     = 11

  validation {
    condition     = var.data_volume_gib >= 8 && var.data_volume_gib <= 50 && floor(var.data_volume_gib) == var.data_volume_gib
    error_message = "data_volume_gib must be a whole number from 8 to 50."
  }
}

variable "root_volume_gib" {
  description = "Size of the root volume in GiB."
  type        = number
  default     = 20

  validation {
    condition     = var.root_volume_gib >= 8 && var.root_volume_gib <= 30 && floor(var.root_volume_gib) == var.root_volume_gib
    error_message = "root_volume_gib must be a whole number from 8 to 30."
  }
}

variable "acme_ca" {
  description = "The certificate authority's ACME directory Caddy gets its certificate from: Let's Encrypt production (default) or, for testing under the rate limits, its staging endpoint. Changing it replaces the instance (user data)."
  type        = string
  default     = "https://acme-v02.api.letsencrypt.org/directory"

  # VERIFY: both directory addresses at D19b.
  validation {
    condition     = contains(["https://acme-v02.api.letsencrypt.org/directory", "https://acme-staging-v02.api.letsencrypt.org/directory"], var.acme_ca)
    error_message = "acme_ca must be the Let's Encrypt production or staging directory address."
  }
}

variable "availability_zone" {
  description = "Availability zone of the instance and its data volume, in us-east-1. Changing it replaces the data volume, which prevent_destroy refuses."
  type        = string
  default     = "us-east-1a"

  validation {
    condition     = can(regex("^us-east-1[a-f]$", var.availability_zone))
    error_message = "availability_zone must be a us-east-1 zone (us-east-1a to us-east-1f)."
  }
}
