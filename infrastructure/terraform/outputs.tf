output "public_ip" {
  description = "Elastic IP of the CloudRescue host."
  value       = aws_eip.host.public_ip
}

output "instance_id" {
  value = aws_instance.host.id
}

output "security_group_id" {
  value = aws_security_group.host.id
}

output "ssh_command" {
  description = "Connect to the VM (use the private key matching ssh_public_key_path)."
  value       = "ssh -i ~/.ssh/cloudrescue ubuntu@${aws_eip.host.public_ip}"
}

output "grafana_url" {
  description = "Grafana URL (only reachable from admin_cidr). Without expose_grafana use the tunnel below."
  value       = var.expose_grafana ? "http://${aws_eip.host.public_ip}:3000" : "not exposed; use the SSH tunnel"
}

output "grafana_tunnel" {
  description = "SSH tunnel to Grafana, then browse http://localhost:3000"
  value       = "ssh -i ~/.ssh/cloudrescue -L 3000:localhost:3000 ubuntu@${aws_eip.host.public_ip}"
}

output "next_steps" {
  value = <<-EOT
    1. Wait ~5 minutes for the bootstrap, then: ssh -i ~/.ssh/cloudrescue ubuntu@${aws_eip.host.public_ip}
    2. Follow progress:   sudo tail -f /var/log/cloudrescue-bootstrap.log
    3. Grafana password:  sudo grep GRAFANA_ADMIN_PASSWORD /opt/cloudrescue/.env
    4. Break something:   cd /opt/cloudrescue && sudo scripts/chaos.sh stop
    5. When finished:     terraform destroy
  EOT
}
