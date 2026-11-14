# What the CI-applied server part (D17b) reads. A name only, never a secret.
output "instance_profile_name" {
  description = "Name of the instance profile D17b attaches to the instance."
  value       = aws_iam_instance_profile.instance.name
}
