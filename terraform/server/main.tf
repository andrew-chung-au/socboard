# The server (issue #199, Deploy item D17b): one EC2 instance in the default VPC, its Elastic IP,
# its security group and its encrypted data volume. The part CI may later apply on `main` with the
# apply role of terraform/ci-access (#156); the human applies it first (D19). Decision record:
# _docs/decisions/2026-11-14T1742Z-server-instance-network-volume.md.
#
# Checked offline only: `fmt`, `validate` and `terraform test` (mock provider, tests/) ran with
# Terraform 1.17.2; nothing has been planned or applied against AWS, so the AWS behaviour below
# is unchecked until the first apply (D19). VERIFY: each one against the pinned provider.
#
# The provider lock file `.terraform.lock.hcl` is not in this folder yet: the human creates it at
# the first `init` and commits it (README), as in the other parts.
#
# Fits #156's apply role, which constrains how this is written: every resource it creates carries
# `project = "socboard"` in its own create call (instance, data volume, security group, Elastic IP);
# the root volume stays untagged (#162 snapshots every volume with that tag); no IAM data source,
# no remote state and no Parameter Store lookup (all refused to the CI roles); no IAM, DLM, budget,
# KMS or Systems Manager resource. Never held here: no secret value, in the user data or anywhere.
#
# The bootstrap (issue #233, D17c) is the user data: bootstrap.sh.tftpl, rendered below. It installs
# Docker, the Compose plugin and Caddy, mounts the data volume and writes Caddy's file.

terraform {
  # One mention of the version besides terraform/.terraform-version (the single source): the same lower bound as terraform/state.
  required_version = ">= 1.11.0"

  required_providers {
    # VERIFY: the major version 6 names every argument used below. The lock file pins the exact one.
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  # Named here on purpose: the region never comes from a default region setting
  # (Deploy decision record, part C). No default tags: they could tag the root volume.
  region = "us-east-1"
}

locals {
  # The tag #156's apply role requires on every create, and #162's snapshot schedule selects
  # (`target_tags` in terraform/iam-and-budgets/main.tf). Changing it stops the backups silently:
  # change iam-and-budgets/ in the same sitting (README).
  tags = { project = "socboard" }

  # Made by terraform/iam-and-budgets/ (#162, output `instance_profile_name`); #156's apply role may
  # pass only this role. Named directly: the CI roles are denied every IAM read and the other state.
  instance_profile_name = "socboard-instance"

  # The only open ports, each way. Opening another port means editing this list; tests/ fail on it.
  ingress_ports = [80, 443]
  # Grooming decision: package installs, image pulls and the Systems Manager agent use HTTPS
  # (80 for some package mirrors). VERIFY: at the first apply that all three work with this.
  egress_ports = [80, 443]

  # Where the bootstrap mounts the data volume; its `postgres` child is the server's Postgres data
  # directory and its `caddy` child Caddy's storage (README, "The bootstrap").
  data_dir = "/srv/socboard-data"

  # The app's port on the host's loopback, the one address Caddy proxies to (compose.yaml publishes it).
  app_upstream = "127.0.0.1:8000"

  # Deploy decision 6: the public hostname is the Elastic IP through a wildcard-DNS service, as
  # 203-0-113-10.sslip.io. VERIFY: sslip.io answers this dashed form with the address.
  hostname = "${replace(aws_eip.server.public_ip, ".", "-")}.sslip.io"
}

# Newest Ubuntu 24.04 LTS x86_64 image from Canonical, read with ec2:DescribeImages (allowed to the
# plan and apply roles). VERIFY: Canonical's owner id and the name pattern.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

# The default subnet of the chosen zone, in the default VPC. This part creates no network: the apply
# role cannot. VERIFY: `default_for_az` selects it; README covers an account with no default VPC.
data "aws_subnet" "default" {
  availability_zone = var.availability_zone
  default_for_az    = true
}

resource "aws_security_group" "server" {
  name        = "socboard-server"
  description = "socboard server - HTTP and HTTPS only"
  vpc_id      = data.aws_subnet.default.vpc_id

  # No inline ingress or egress blocks: the rules below are the only ones. The provider removes the
  # allow-all egress rule AWS adds to a new group (VERIFY).
  tags = local.tags
}

# Rules carry no tags: the apply role can tag only the instance, volume, group and Elastic IP.
resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each = toset([for p in local.ingress_ports : tostring(p)])

  security_group_id = aws_security_group.server.id
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "web" {
  for_each = toset([for p in local.egress_ports : tostring(p)])

  security_group_id = aws_security_group.server.id
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  cidr_ipv4         = "0.0.0.0/0"
}

# No key pair. User data is readable by the CI plan role (ec2:Describe*), so it must never hold a
# secret; the bootstrap holds none.
resource "aws_instance" "server" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnet.default.id
  vpc_security_group_ids = [aws_security_group.server.id]
  iam_instance_profile   = local.instance_profile_name

  user_data = templatefile("${path.module}/bootstrap.sh.tftpl", {
    data_dir     = local.data_dir
    app_upstream = local.app_upstream
    # The volume's NVMe serial is its id without the dash. VERIFY: the link name on noble.
    data_device = "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${replace(aws_ebs_volume.data.id, "-", "")}"
    hostname    = local.hostname
    acme_ca     = var.acme_ca
  })
  # cloud-init runs user data once per instance, so changed user data on the same instance would
  # never run. Replacing the instance runs it; the data volume and the Elastic IP are separate
  # resources and survive, and Caddy's certificate is on the volume (README, "The bootstrap").
  user_data_replace_on_change = true

  # T3 defaults to unlimited credits, which bill extra; standard caps the cost.
  credit_specification {
    cpu_credits = "standard"
  }

  # Session tokens required, hop limit 1: a container on a bridge network is one hop further and
  # cannot reach the instance's credentials.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  # No tags here and no volume tags on the instance: the root volume stays untagged (#162).
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gib
    encrypted             = true
    delete_on_termination = true
  }

  tags = local.tags

  # A newer published image must not replace the server on an ordinary plan.
  lifecycle {
    ignore_changes = [ami]
  }
}

# Keeps the address (and the hostname) when the instance is replaced: only the association moves.
# The association is its own resource because the user data names the address: an `instance`
# argument here would make the address depend on the instance and the instance on the address.
resource "aws_eip" "server" {
  domain = "vpc"

  tags = local.tags
}

resource "aws_eip_association" "server" {
  allocation_id = aws_eip.server.id
  instance_id   = aws_instance.server.id
}

# The database's disk. Encrypted with the AWS-managed EBS key (no customer key: #156 grants no KMS
# action). In the instance's zone; a changed zone means replacement, which prevent_destroy refuses.
# Teardown (D23) removes the guard by hand (README).
resource "aws_ebs_volume" "data" {
  availability_zone = var.availability_zone
  type              = "gp3"
  size              = var.data_volume_gib
  encrypted         = true

  tags = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

# The instance is stopped before a detach, so Postgres is shut down before its disk leaves.
# VERIFY: `stop_instance_before_detaching`; on a Nitro instance the device shows as NVMe, so the
# bootstrap finds it by its volume id and mounts it by file-system UUID.
resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.server.id
  stop_instance_before_detaching = true
}
