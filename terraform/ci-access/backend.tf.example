# Step 3 of the ci-access section of terraform/README.md copies this to backend.tf.
# The bucket (made by terraform/state) is not named here: it is passed at every `init` with
# -backend-config="bucket=<name>", so no name is committed. This part has its own key, outside the
# `server/` prefix, so the CI roles cannot read or write it.
terraform {
  backend "s3" {
    # VERIFY: `key`, `region`, `encrypt` and `use_lockfile` are s3 backend argument names in Terraform 1.17.
    key          = "ci-access/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
