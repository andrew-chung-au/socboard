# Identifiers only, never a secret (decision record of #139, section F).

output "elastic_ip" {
  description = "The server's public address. It stays the same when the instance is replaced."
  value       = aws_eip.server.public_ip
}

output "hostname" {
  description = "The public hostname Caddy serves, derived from the Elastic IP (Deploy decision 6)."
  value       = local.hostname
}

output "prod_hostname" {
  description = "The public hostname Caddy serves for the prod stack: the dev hostname with a prod- prefix (issue #266). No prod app runs behind it until the promote workflow deploys one."
  value       = local.prod_hostname
}

output "instance_id" {
  description = "The instance id, for Systems Manager and the deploy role."
  value       = aws_instance.server.id
}

output "data_volume_id" {
  description = "The data volume id, to find its snapshots."
  value       = aws_ebs_volume.data.id
}
