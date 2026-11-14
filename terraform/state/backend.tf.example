# Step 5 of terraform/README.md copies this to backend.tf, after the bucket exists.
# Not named backend.tf yet: with it present, the first apply would look for a bucket that is not there.
# The bucket name is not here: it is passed at `init` with -backend-config, so no name is committed twice.
terraform {
  backend "s3" {
    # VERIFY: `key`, `region`, `encrypt` and `use_lockfile` are s3 backend argument names in Terraform 1.17.
    key          = "state/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
