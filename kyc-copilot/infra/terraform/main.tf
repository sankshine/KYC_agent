# ============================================================
# KYC Copilot - AWS Infrastructure (Terraform)
# Target: Canada Central (ca-central-1) for PIPEDA compliance
# ============================================================

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  
  backend "s3" {
    bucket = "kyc-copilot-terraform-state"
    key    = "prod/terraform.tfstate"
    region = "ca-central-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.aws_region
  
  default_tags {
    tags = {
      Project     = "kyc-copilot"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# ── Variables ────────────────────────────────────────────────
variable "aws_region" {
  default = "ca-central-1"
}

variable "environment" {
  default = "prod"
}

variable "cluster_name" {
  default = "kyc-copilot-eks"
}

# ── Networking ───────────────────────────────────────────────
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"
  
  name = "kyc-copilot-vpc"
  cidr = "10.0.0.0/16"
  
  azs             = ["ca-central-1a", "ca-central-1b"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]
  
  enable_nat_gateway = true
  single_nat_gateway = false  # HA for prod
  
  enable_dns_hostnames = true
  enable_dns_support   = true
}

# ── EKS Cluster ──────────────────────────────────────────────
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"
  
  cluster_name    = var.cluster_name
  cluster_version = "1.29"
  
  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets
  
  cluster_endpoint_public_access = true
  
  eks_managed_node_groups = {
    # General workloads
    general = {
      min_size     = 2
      max_size     = 10
      desired_size = 3
      
      instance_types = ["t3.xlarge"]
      capacity_type  = "ON_DEMAND"
    }
    
    # GPU nodes for vision model inference (optional, for self-hosted models)
    # gpu = {
    #   min_size     = 0
    #   max_size     = 2
    #   desired_size = 0
    #   instance_types = ["g4dn.xlarge"]
    # }
  }
}

# ── S3 Buckets ───────────────────────────────────────────────
resource "aws_s3_bucket" "kyc_documents" {
  bucket = "kyc-copilot-documents-${var.environment}"
}

resource "aws_s3_bucket_versioning" "kyc_documents" {
  bucket = aws_s3_bucket.kyc_documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kyc_documents" {
  bucket = aws_s3_bucket.kyc_documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Auto-delete documents after 24 hours (privacy compliance)
resource "aws_s3_bucket_lifecycle_configuration" "kyc_documents" {
  bucket = aws_s3_bucket.kyc_documents.id
  rule {
    id     = "auto-delete-validations"
    status = "Enabled"
    filter { prefix = "validations/" }
    expiration { days = 1 }
  }
}

resource "aws_s3_bucket_public_access_block" "kyc_documents" {
  bucket                  = aws_s3_bucket.kyc_documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── RDS PostgreSQL ───────────────────────────────────────────
resource "aws_db_instance" "kyc_db" {
  identifier = "kyc-copilot-db"
  
  engine         = "postgres"
  engine_version = "16.1"
  instance_class = "db.t3.medium"
  
  allocated_storage     = 100
  max_allocated_storage = 500
  storage_encrypted     = true
  
  db_name  = "kyc_copilot"
  username = "kyc_admin"
  password = var.db_password  # Provided via secrets manager
  
  multi_az               = true  # HA for prod
  publicly_accessible    = false
  vpc_security_group_ids = [aws_security_group.db.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  
  backup_retention_period = 30
  deletion_protection     = true
  
  enabled_cloudwatch_logs_exports = ["postgresql"]
}

variable "db_password" {
  sensitive = true
}

resource "aws_db_subnet_group" "main" {
  name       = "kyc-copilot-db-subnet"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "db" {
  name   = "kyc-copilot-db-sg"
  vpc_id = module.vpc.vpc_id
  
  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = module.vpc.private_subnets_cidr_blocks
  }
}

# ── ElastiCache Redis ────────────────────────────────────────
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "kyc-copilot-redis"
  description          = "KYC Copilot Redis cache and job queue"
  
  node_type            = "cache.t3.medium"
  num_cache_clusters   = 2  # Primary + replica
  automatic_failover_enabled = true
  
  subnet_group_name    = aws_elasticache_subnet_group.redis.name
  security_group_ids   = [aws_security_group.redis.id]
  
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
}

resource "aws_elasticache_subnet_group" "redis" {
  name       = "kyc-copilot-redis-subnet"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name   = "kyc-copilot-redis-sg"
  vpc_id = module.vpc.vpc_id
  
  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = module.vpc.private_subnets_cidr_blocks
  }
}

# ── Outputs ──────────────────────────────────────────────────
output "eks_cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "s3_bucket_name" {
  value = aws_s3_bucket.kyc_documents.bucket
}

output "rds_endpoint" {
  value     = aws_db_instance.kyc_db.endpoint
  sensitive = true
}
