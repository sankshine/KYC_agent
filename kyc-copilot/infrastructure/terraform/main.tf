terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" { region = "ca-central-1" }

resource "aws_ecs_cluster" "kyc_copilot" { name = "kyc-copilot-cluster" }

resource "random_string" "suffix" { length = 8; special = false; upper = false }

resource "aws_s3_bucket" "temp_docs" {
  bucket = "kyc-copilot-temp-docs-${random_string.suffix.result}"
}

resource "aws_s3_bucket_lifecycle_configuration" "temp_docs" {
  bucket = aws_s3_bucket.temp_docs.id
  rule {
    id     = "auto-delete-24h"
    status = "Enabled"
    expiration { days = 1 }
  }
}

resource "aws_elasticache_cluster" "cache" {
  cluster_id      = "kyc-copilot-cache"
  engine          = "redis"
  node_type       = "cache.t3.micro"
  num_cache_nodes = 1
  port            = 6379
}
