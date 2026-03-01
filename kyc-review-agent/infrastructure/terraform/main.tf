# KYC Review Agent — AWS Infrastructure
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  backend "s3" {
    bucket = "kyc-terraform-state"
    key    = "kyc-review-agent/terraform.tfstate"
    region = "ca-central-1"
  }
}

provider "aws" { region = var.aws_region }

variable "aws_region" { default = "ca-central-1" }
variable "environment" { default = "production" }

# VPC
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  tags = { Name = "kyc-ai-vpc", Environment = var.environment }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 1}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = { Name = "kyc-private-${count.index + 1}" }
}

data "aws_availability_zones" "available" {}

# ECS Cluster
resource "aws_ecs_cluster" "kyc_ai" {
  name = "kyc-ai-cluster-${var.environment}"
  setting { name = "containerInsights", value = "enabled" }
}

# Secrets Manager for Anthropic API Key
resource "aws_secretsmanager_secret" "anthropic_key" {
  name                    = "kyc-ai/anthropic-api-key"
  recovery_window_in_days = 7
}

# S3 Bucket with encryption and lifecycle
resource "aws_s3_bucket" "kyc_documents" {
  bucket = "kyc-documents-${var.environment}-${random_string.suffix.result}"
}

resource "random_string" "suffix" {
  length  = 8
  special = false
  upper   = false
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kyc_docs" {
  bucket = aws_s3_bucket.kyc_documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "kyc_docs" {
  bucket = aws_s3_bucket.kyc_documents.id
  rule {
    id     = "delete-temp"
    status = "Enabled"
    filter { prefix = "temp/" }
    expiration { days = 1 }
  }
  rule {
    id     = "archive-reviewed"
    status = "Enabled"
    filter { prefix = "reviewed/" }
    expiration { days = 90 }
  }
}

# ElastiCache Redis
resource "aws_elasticache_cluster" "cache" {
  cluster_id           = "kyc-cache-${var.environment}"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
}

# SQS Queue for review submissions
resource "aws_sqs_queue" "review_queue" {
  name                       = "kyc-review-queue-${var.environment}"
  visibility_timeout_seconds = 300
  message_retention_seconds  = 86400
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "dlq" {
  name = "kyc-review-dlq-${var.environment}"
}

# SNS for fraud alerts
resource "aws_sns_topic" "fraud_alerts" {
  name = "kyc-fraud-alerts-${var.environment}"
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "kyc_ai" {
  name              = "/ecs/kyc-ai-${var.environment}"
  retention_in_days = 30
}

output "sqs_queue_url" { value = aws_sqs_queue.review_queue.url }
output "s3_bucket_name" { value = aws_s3_bucket.kyc_documents.id }
output "redis_endpoint" { value = aws_elasticache_cluster.cache.cache_nodes[0].address }
