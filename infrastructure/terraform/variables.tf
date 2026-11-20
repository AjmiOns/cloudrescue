variable "aws_region" {
  description = "AWS region. Learner Lab usually allows us-east-1 and us-west-2 only."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "cloudrescue"
}

variable "environment" {
  description = "Environment label used in names and tags."
  type        = string
  default     = "lab"
}

variable "repo_url" {
  description = "HTTPS URL of the (public) Git repository the VM clones, e.g. https://github.com/<you>/cloudrescue.git"
  type        = string

  validation {
    condition     = can(regex("^https://[A-Za-z0-9./_-]+$", var.repo_url))
    error_message = "repo_url must be a plain https:// URL (letters, digits, . / _ - only)."
  }
}

variable "repo_ref" {
  description = "Branch or tag to deploy."
  type        = string
  default     = "main"

  validation {
    condition     = can(regex("^[A-Za-z0-9._/-]+$", var.repo_ref))
    error_message = "repo_ref may contain only letters, digits, . _ / and -."
  }
}

variable "admin_cidr" {
  description = "Your public IP as a CIDR (e.g. 203.0.113.7/32). The ONLY source allowed to reach SSH and Grafana."
  type        = string

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0))
    error_message = "admin_cidr must be a valid CIDR block such as 203.0.113.7/32."
  }

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && tonumber(split("/", var.admin_cidr)[1]) >= 24
    error_message = "admin_cidr must be /24 or narrower. Opening the lab to the world (0.0.0.0/0) is not allowed."
  }
}

variable "ssh_public_key_path" {
  description = "Path to the SSH PUBLIC key to install (generate: ssh-keygen -t ed25519 -f ~/.ssh/cloudrescue). Terraform never sees the private key."
  type        = string
  default     = "~/.ssh/cloudrescue.pub"
}

variable "instance_type" {
  description = "EC2 instance type. t3.small (2 GB) is the practical minimum for Prometheus + Grafana + the stack."
  type        = string
  default     = "t3.small"
}

variable "root_volume_gb" {
  description = "Root EBS volume size (encrypted gp3)."
  type        = number
  default     = 20
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "subnet_cidr" {
  type    = string
  default = "10.0.1.0/24"
}

variable "availability_zone" {
  description = "Optional AZ. Defaults to the first available one."
  type        = string
  default     = null
}

variable "expose_grafana" {
  description = "Open port 3000 to admin_cidr. If false, reach Grafana through an SSH tunnel (more secure: Grafana is plain HTTP)."
  type        = bool
  default     = true
}

variable "iam_instance_profile" {
  description = "Existing instance profile to attach. Learner Lab forbids creating IAM roles, but provides LabInstanceProfile. Set to null to attach nothing."
  type        = string
  default     = "LabInstanceProfile"
}

variable "enable_l2_reboot" {
  description = "Enable level-2 recovery (the monitor reboots this VM through the EC2 API). Raises the IMDS hop limit to 2 so a container can reach instance credentials; read docs/security.md first."
  type        = bool
  default     = false
}
