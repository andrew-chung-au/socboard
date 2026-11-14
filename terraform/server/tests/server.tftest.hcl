# Offline checks of terraform/server (issue #199). A mock AWS provider: no credential, no network
# to AWS, nothing created. Every run is a plan, and the assertions read the planned values, so they
# check the configuration's shape, not AWS behaviour (the VERIFY: items stay open until D19).
# shortcut: a test can only read resources it names, so a second rule resource under another name
# (not the `web` list) is not seen here; review catches it. Upgrade if rules ever come from elsewhere.

mock_provider "aws" {}

# The data sources' computed values, so the plan has known ids (the mock would invent them anyway).
override_data {
  target = data.aws_ami.ubuntu
  values = {
    id = "ami-0123456789abcdef0"
  }
}

override_data {
  target = data.aws_subnet.default
  values = {
    id     = "subnet-0123456789abcdef0"
    vpc_id = "vpc-0123456789abcdef0"
  }
}

# `key_name` and the group's inline rule sets are computed, so a plan leaves them unknown when the
# configuration does not set them. These overrides make them null and empty at plan time; a value the
# configuration sets still wins over an override, so adding `key_name` or an inline rule fails below.
override_resource {
  target          = aws_instance.server
  override_during = plan
  values = {
    key_name = null
  }
}

override_resource {
  target          = aws_security_group.server
  override_during = plan
  values = {
    ingress = []
    egress  = []
  }
}

run "defaults" {
  command = plan

  # Network: exactly TCP 80 and 443 from anywhere in, and exactly TCP 80 and 443 out.
  assert {
    condition     = toset([for r in aws_vpc_security_group_ingress_rule.web : r.from_port]) == toset([80, 443]) && length(aws_vpc_security_group_ingress_rule.web) == 2
    error_message = "Ingress must open exactly TCP 80 and 443."
  }

  assert {
    condition     = alltrue([for r in aws_vpc_security_group_ingress_rule.web : r.to_port == r.from_port && r.ip_protocol == "tcp" && r.cidr_ipv4 == "0.0.0.0/0"])
    error_message = "Each ingress rule must be one TCP port from 0.0.0.0/0."
  }

  assert {
    condition     = toset([for r in aws_vpc_security_group_egress_rule.web : r.from_port]) == toset([80, 443]) && length(aws_vpc_security_group_egress_rule.web) == 2
    error_message = "Egress must open exactly TCP 80 and 443."
  }

  assert {
    condition     = alltrue([for r in aws_vpc_security_group_egress_rule.web : r.to_port == r.from_port && r.ip_protocol == "tcp" && r.cidr_ipv4 == "0.0.0.0/0"])
    error_message = "Each egress rule must be one TCP port to 0.0.0.0/0."
  }

  assert {
    condition     = length(aws_security_group.server.ingress) == 0 && length(aws_security_group.server.egress) == 0
    error_message = "The security group must have no inline ingress or egress rules."
  }

  # Metadata: tokens required, hop limit 1, instance tags off.
  assert {
    condition     = aws_instance.server.metadata_options[0].http_tokens == "required" && aws_instance.server.metadata_options[0].http_put_response_hop_limit == 1 && aws_instance.server.metadata_options[0].instance_metadata_tags == "disabled"
    error_message = "Metadata must require tokens with a hop limit of 1 and instance tags off."
  }

  # No key pair.
  assert {
    condition     = aws_instance.server.key_name == null
    error_message = "The instance must have no key pair."
  }

  # Data volume: encrypted gp3, tagged for the snapshot schedule, no customer key.
  assert {
    condition     = aws_ebs_volume.data.encrypted == true && aws_ebs_volume.data.type == "gp3" && aws_ebs_volume.data.size == 10
    error_message = "The data volume must be an encrypted 10 GiB gp3 volume by default."
  }

  assert {
    condition     = aws_ebs_volume.data.tags == tomap({ project = "socboard" })
    error_message = "The data volume must carry project = socboard."
  }

  # Root volume: encrypted gp3, deleted with the instance, and no project tag anywhere that reaches it.
  assert {
    condition     = aws_instance.server.root_block_device[0].encrypted == true && aws_instance.server.root_block_device[0].volume_type == "gp3" && aws_instance.server.root_block_device[0].delete_on_termination == true
    error_message = "The root volume must be encrypted gp3 and deleted with the instance."
  }

  assert {
    condition     = !contains(keys(coalesce(aws_instance.server.root_block_device[0].tags, {})), "project") && aws_instance.server.volume_tags == null
    error_message = "The root volume must not carry the project tag (no root_block_device tags, no volume_tags)."
  }

  # The create-call tags #156's apply role requires.
  assert {
    condition     = alltrue([for t in [aws_instance.server.tags, aws_security_group.server.tags, aws_eip.server.tags] : t == tomap({ project = "socboard" })])
    error_message = "The instance, security group and Elastic IP must carry project = socboard."
  }

  # Cost and placement defaults.
  assert {
    condition     = aws_instance.server.instance_type == "t3.small" && aws_instance.server.credit_specification[0].cpu_credits == "standard"
    error_message = "The default must be a t3.small with standard CPU credits."
  }

  assert {
    condition     = aws_instance.server.iam_instance_profile == "socboard-instance"
    error_message = "The instance profile must be socboard-instance."
  }
}

run "refuses_a_large_instance_type" {
  command = plan

  variables {
    instance_type = "m5.large"
  }

  expect_failures = [var.instance_type]
}

run "refuses_a_500_gib_data_volume" {
  command = plan

  variables {
    data_volume_gib = 500
  }

  expect_failures = [var.data_volume_gib]
}

run "refuses_a_zone_outside_us_east_1" {
  command = plan

  variables {
    availability_zone = "us-west-2a"
  }

  expect_failures = [var.availability_zone]
}
