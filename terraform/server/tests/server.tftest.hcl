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
# The instance's and the Elastic IP's ids are fixed too, so the association can be compared (#256).
override_resource {
  target          = aws_instance.server
  override_during = plan
  values = {
    id       = "i-0123456789abcdef0"
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

# Known at plan, so the rendered user data (which names both) can be read below (issue #233).
# `instance` is null unless the configuration sets it, as with `key_name` above.
override_resource {
  target          = aws_eip.server
  override_during = plan
  values = {
    id        = "eipalloc-0123456789abcdef0"
    public_ip = "203.0.113.10"
    instance  = null
  }
}

override_resource {
  target          = aws_ebs_volume.data
  override_during = plan
  values = {
    id = "vol-0123456789abcdef0"
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

  # The bootstrap (issue #233). Changed user data replaces the instance, so it runs.
  assert {
    condition     = aws_instance.server.user_data_replace_on_change == true
    error_message = "user_data_replace_on_change must be true (cloud-init never re-runs user data on the same instance)."
  }

  # The hostname is built from the Elastic IP, and Caddy serves it to the app's loopback port only.
  assert {
    condition     = output.hostname == "203-0-113-10.sslip.io" && strcontains(aws_instance.server.user_data, "\n203-0-113-10.sslip.io {\n\treverse_proxy 127.0.0.1:8000\n}")
    error_message = "Caddy must serve the hostname derived from the Elastic IP and proxy only to 127.0.0.1:8000."
  }

  assert {
    condition     = strcontains(aws_instance.server.user_data, "\tadmin off\n") && strcontains(aws_instance.server.user_data, "\tacme_ca https://acme-v02.api.letsencrypt.org/directory\n")
    error_message = "Caddy's admin endpoint must be off and the default certificate authority production."
  }

  # The volume: found by its id, mounted by UUID with nofail and the 30-second device wait at
  # /srv/socboard-data, and formatted only when blkid finds no signature.
  assert {
    condition = alltrue([for s in [
      "DATA_DEV='/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol0123456789abcdef0'",
      "DATA_DIR='/srv/socboard-data'",
      "\"UUID=$uuid $DATA_DIR ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2\"",
      "if [ \"$rc\" -eq 2 ]; then",
    ] : strcontains(aws_instance.server.user_data, s)])
    error_message = "The bootstrap must mount the data volume by id and UUID with nofail at /srv/socboard-data and format only an empty one."
  }

  # The layout on the volume (#256): the mount root root:root 0755 on every run, Postgres's directory
  # (STACK_PGDATA_DIR on the server) 999 and 0700 on its top level, Caddy's folder owned by the caddy
  # user recursively with 0700, and the caddy uid never 999.
  assert {
    condition = alltrue([for s in [
      "PG_DIR='/srv/socboard-data/postgres'\n",
      "CADDY_DIR='/srv/socboard-data/caddy'\n",
      "\nown \"$DATA_DIR\" 0:0 755\n",
      "\nown \"$PG_DIR\" 999:999 700\n",
      "\nown \"$CADDY_DIR\" \"$caddy_uid:$caddy_gid\" 700\n",
      "\n  chown -R \"$caddy_uid:$caddy_gid\" \"$CADDY_DIR\"\n",
      "if [ \"$caddy_uid\" = 999 ]; then",
    ] : strcontains(aws_instance.server.user_data, s)])
    error_message = "The bootstrap must set the mount root to root:root 0755, postgres/ to 999 0700 and caddy/ to the caddy user (recursively) 0700."
  }

  # Caddy's storage is its folder on the volume, beside the Postgres directory and not under it, so no
  # container mount reaches Caddy's keys (F-233-5).
  assert {
    condition     = strcontains(aws_instance.server.user_data, "\t\troot /srv/socboard-data/caddy\n") && !startswith(regex("CADDY_DIR='([^']*)'", aws_instance.server.user_data)[0], "${regex("PG_DIR='([^']*)'", aws_instance.server.user_data)[0]}/") && !strcontains(aws_instance.server.user_data, "/srv/socboard-data/postgres/caddy")
    error_message = "Caddy's storage root must be /srv/socboard-data/caddy, outside the Postgres directory."
  }

  # Docker and Caddy each wait for the data mount and do not start without it (F-233-4).
  assert {
    condition = alltrue([for f in ["docker", "caddy"] :
    strcontains(aws_instance.server.user_data, "write /etc/systemd/system/${f}.service.d/socboard.conf 0644 <<'EOF'\n[Unit]\nRequiresMountsFor=/srv/socboard-data\nEOF\n")])
    error_message = "Docker and Caddy must each have a drop-in with RequiresMountsFor=/srv/socboard-data."
  }

  # Only the pinned Caddy key: exactly one primary key with this fingerprint (F-233-6).
  assert {
    condition     = strcontains(aws_instance.server.user_data, "CADDY_KEY_FPR='65760C51EDEA2017CEA2CA15155B6D79CA56EA34'") && strcontains(aws_instance.server.user_data, "if [ \"$found\" = \"$CADDY_KEY_FPR\" ]; then return 0; fi") && strcontains(aws_instance.server.user_data, "if ! caddy_key_ok \"$CADDY_KEYRING.new\"; then")
    error_message = "The Caddy keyring must be accepted only with exactly one primary key of the pinned fingerprint."
  }

  # No hidden write failure (F-233-7): files are written only through `write`, which stops on a failed
  # put; nothing swallows a failure with `|| true`, and put's one caller is write.
  assert {
    condition     = !strcontains(aws_instance.server.user_data, "|| true") && length(regexall("(?m)^\\s*(if !? *)?put ", aws_instance.server.user_data)) == 1 && strcontains(aws_instance.server.user_data, "\n  put \"$@\" || rc=$?\n") && strcontains(aws_instance.server.user_data, "    *)\n      say \"could not write $1; stopping\"\n      exit 1\n")
    error_message = "Every file write must go through write, which stops the script on a failed write."
  }

  # Log rotation and the prune that only removes unused images.
  assert {
    condition     = strcontains(aws_instance.server.user_data, "\"max-size\": \"10m\", \"max-file\": \"3\"") && strcontains(aws_instance.server.user_data, "docker image prune --all --force --filter until=168h")
    error_message = "The bootstrap must rotate Docker's logs (10m, 3 files) and prune only unused images."
  }

  # Never held: no secret in the user data. These are the patterns of the kit's secret scan
  # (_docs/agent-kit/scripts/secret-patterns.txt) that can match a value; the file sits outside this
  # folder, so tf-check's copy cannot read it. This copy must follow that file: a pattern added there
  # is added here. And no Secrets Manager call.
  assert {
    condition = alltrue([for p in [
      "-----BEGIN [A-Z ]*PRIVATE KEY-----",
      "-----BEGIN PGP PRIVATE KEY BLOC[K]-----", # [K]: the same regex, but this line does not match itself
      "(^|[^A-Za-z0-9])sk-(proj|ant)-[A-Za-z0-9_-]{20,}",
      "ghp_[A-Za-z0-9]{36}",
      "github_pat_[A-Za-z0-9_]{22,}",
      "AKIA[0-9A-Z]{16}",
      "xox[baprs]-[A-Za-z0-9-]{10,}",
      "sk-[A-Za-z0-9]{20,}",
      "gh[osur]_[A-Za-z0-9]{36}",
      "([Aa]uthorization|AUTHORIZATION): ([Bb]earer|BEARER|[Bb]asic|BASIC) [A-Za-z0-9._~+/=-]{16,}",
      "eyJ[A-Za-z0-9_-]{7,}\\.eyJ[A-Za-z0-9_-]{7,}\\.[A-Za-z0-9_-]{10,}",
      "hooks\\.slack\\.com/services/",
      "(?i)aws_secret_access_key",
    ] : !can(regex(p, aws_instance.server.user_data))]) && !strcontains(lower(aws_instance.server.user_data), "secretsmanager")
    error_message = "The user data must hold no secret and make no Secrets Manager call."
  }

  # The address is associated separately (the user data names it), and the instance still has it.
  assert {
    condition     = aws_eip.server.instance == null
    error_message = "The Elastic IP must be associated through aws_eip_association, not its instance argument."
  }

  assert {
    condition     = aws_eip_association.server.allocation_id == aws_eip.server.id && aws_eip_association.server.instance_id == aws_instance.server.id
    error_message = "aws_eip_association.server must associate aws_eip.server with aws_instance.server."
  }
}

run "staging_certificate_authority" {
  command = plan

  variables {
    acme_ca = "https://acme-staging-v02.api.letsencrypt.org/directory"
  }

  assert {
    condition     = strcontains(aws_instance.server.user_data, "\tacme_ca https://acme-staging-v02.api.letsencrypt.org/directory\n")
    error_message = "acme_ca must reach Caddy's file."
  }
}

run "refuses_another_certificate_authority" {
  command = plan

  variables {
    acme_ca = "https://acme.example.com/directory"
  }

  expect_failures = [var.acme_ca]
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

# The deploy document (issue #237): one Command document, one parameter `digest` with the digest
# pattern, and the script as its only step. A second parameter, another name, type or pattern, a
# second step or a changed script header fails here.
run "deploy_document" {
  command = plan

  assert {
    condition     = aws_ssm_document.deploy.name == "socboard-deploy" && aws_ssm_document.deploy.document_type == "Command" && aws_ssm_document.deploy.document_format == "JSON"
    error_message = "The document must be a JSON Command document named socboard-deploy."
  }

  assert {
    condition     = keys(jsondecode(aws_ssm_document.deploy.content).parameters) == ["digest"] && jsondecode(aws_ssm_document.deploy.content).parameters.digest.type == "String" && jsondecode(aws_ssm_document.deploy.content).parameters.digest.allowedPattern == "^sha256:[0-9a-f]{64}$"
    error_message = "The document must have exactly one parameter, digest, a String with the pattern ^sha256:[0-9a-f]{64}$."
  }

  assert {
    condition     = keys(jsondecode(aws_ssm_document.deploy.content).parameters.digest) == ["allowedPattern", "description", "type"]
    error_message = "The digest parameter must set only its type, description and allowedPattern."
  }

  assert {
    condition     = length(jsondecode(aws_ssm_document.deploy.content).mainSteps) == 1 && jsondecode(aws_ssm_document.deploy.content).mainSteps[0].action == "aws:runShellScript"
    error_message = "The document must have one step, aws:runShellScript."
  }

  # The step is the script: a shebang first, the one parameter reference, and no other doubled brace
  # (Systems Manager would read one as a parameter reference).
  assert {
    condition = (
      jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand[0] == "#!/bin/bash"
      && strcontains(join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand), "DIGEST_PARAM='{{ digest }}'\n")
      && length(split("{{", join("\n", jsondecode(aws_ssm_document.deploy.content).mainSteps[0].inputs.runCommand))) == 2
    )
    error_message = "The step must be the script: first line #!/bin/bash, exactly one parameter reference, {{ digest }}."
  }

  # Never held: no account number (twelve digits), no ARN, no secret pattern of the kit's scan.
  assert {
    condition = alltrue([for p in [
      "(^|[^0-9])[0-9]{12}([^0-9]|$)",
      "(^|[^A-Za-z])arn:",
      "-----BEGIN [A-Z ]*PRIVATE KEY-----",
      "ghp_[A-Za-z0-9]{36}",
      "AKIA[0-9A-Z]{16}",
    ] : !can(regex(p, aws_ssm_document.deploy.content))])
    error_message = "The document must hold no account number, ARN or secret."
  }

  assert {
    condition     = aws_ssm_document.deploy.tags == tomap({ project = "socboard" })
    error_message = "The document must carry project = socboard."
  }
}
