variable "bucket_name" {
  description = "Name of the state bucket. Bucket names are global; choose your own and keep any AWS account number out of it."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be 3 to 63 characters: lowercase letters, digits, dots and hyphens, starting and ending with a letter or digit."
  }
}
