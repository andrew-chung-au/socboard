# Step 2 of the iam-and-budgets section of terraform/README.md copies this to backend.tf.
# The bucket (made by terraform/state) is not named here: it is passed at every `init` with
# -backend-config="bucket=<name>", so no name is committed. This part has its own key.
terraform {
  backend "s3" {
    # VERIFY: `key`, `region`, `encrypt` and `use_lockfile` are s3 backend argument names in Terraform 1.17.
    key          = "iam-and-budgets/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
