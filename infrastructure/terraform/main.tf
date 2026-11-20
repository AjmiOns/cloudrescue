data "aws_availability_zones" "available" {
  state = "available"
}

# Canonical's official Ubuntu 22.04 LTS image.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  name = "${var.project_name}-${var.environment}"
  az   = coalesce(var.availability_zone, data.aws_availability_zones.available.names[0])

  # Egress is restricted to what the VM needs: package mirrors / git / registries (80, 443) and time sync.
  egress_rules = {
    http  = { port = 80, protocol = "tcp", description = "apt mirrors" }
    https = { port = 443, protocol = "tcp", description = "git, docker registry, apt" }
    ntp   = { port = 123, protocol = "udp", description = "time sync" }
  }
}

# --- Network ------------------------------------------------------------------------------
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${local.name}-vpc" }
}

# The default security group allows all traffic between its members. Strip every rule so nothing
# can accidentally rely on it.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${local.name}-default-locked" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = { Name = "${local.name}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.subnet_cidr
  availability_zone       = local.az
  map_public_ip_on_launch = false # the Elastic IP below is the only public address

  tags = { Name = "${local.name}-public-${local.az}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# --- Security group: least privilege ---------------------------------------------------------
resource "aws_security_group" "host" {
  name_prefix = "${local.name}-host-"
  description = "CloudRescue host: admin-only ingress, restricted egress"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name}-sg" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.host.id
  description       = "SSH from the administrator only"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "grafana" {
  count = var.expose_grafana ? 1 : 0

  security_group_id = aws_security_group.host.id
  description       = "Grafana (HTTP) from the administrator only"
  cidr_ipv4         = var.admin_cidr
  ip_protocol       = "tcp"
  from_port         = 3000
  to_port           = 3000
}

resource "aws_vpc_security_group_egress_rule" "allowed" {
  for_each = local.egress_rules

  security_group_id = aws_security_group.host.id
  description       = each.value.description
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = each.value.protocol
  from_port         = each.value.port
  to_port           = each.value.port
}

# --- Compute ------------------------------------------------------------------------------
resource "aws_key_pair" "admin" {
  key_name   = "${local.name}-admin"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

resource "aws_instance" "host" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.host.id]
  key_name               = aws_key_pair.admin.key_name
  iam_instance_profile   = var.iam_instance_profile

  # IMDSv2 only (token required) defeats the classic SSRF -> credential theft path.
  # Hop limit 1 keeps containers away from instance credentials; level-2 recovery needs 2.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = var.enable_l2_reboot ? 2 : 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  # Secrets are generated on the VM at first boot, so they are never in user_data or in Terraform state.
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    repo_url         = var.repo_url
    repo_ref         = var.repo_ref
    grafana_bind     = var.expose_grafana ? "0.0.0.0" : "127.0.0.1"
    enable_l2_reboot = var.enable_l2_reboot
  })
  user_data_replace_on_change = true

  tags = { Name = "${local.name}-host" }

  lifecycle {
    ignore_changes = [ami] # do not replace the VM just because Canonical published a newer image
  }
}

resource "aws_eip" "host" {
  domain   = "vpc"
  instance = aws_instance.host.id

  tags = { Name = "${local.name}-eip" }

  depends_on = [aws_internet_gateway.this]
}
