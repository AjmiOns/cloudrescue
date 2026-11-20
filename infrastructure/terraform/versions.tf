terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # Credentials come from the environment / ~/.aws/credentials (Learner Lab gives a temporary
  # access key, secret AND session token: never put them in a .tf or .tfvars file).
  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
