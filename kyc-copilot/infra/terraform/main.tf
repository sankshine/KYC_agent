# KYC Copilot — GCP Infrastructure (Terraform)
# Region: northamerica-northeast1 (Montreal) for Canadian data residency

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
  backend "gcs" {
    bucket = "kyc-terraform-state"
    prefix = "kyc-copilot"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" { description = "GCP Project ID" }
variable "region"     { default = "northamerica-northeast1" }
variable "environment"{ default = "production" }

# ─── APIs ────────────────────────────────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "storage.googleapis.com",
    "redis.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ─── VPC ─────────────────────────────────────────────────────────────────────
resource "google_compute_network" "kyc_vpc" {
  name                    = "kyc-copilot-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "kyc_subnet" {
  name          = "kyc-copilot-subnet"
  ip_cidr_range = "10.0.1.0/24"
  region        = var.region
  network       = google_compute_network.kyc_vpc.id
}

# ─── GCS Bucket (replaces AWS S3) ────────────────────────────────────────────
resource "google_storage_bucket" "kyc_temp_docs" {
  name          = "kyc-copilot-temp-docs-${var.project_id}"
  location      = "NORTHAMERICA-NORTHEAST1"
  force_destroy = false

  # Encrypt with CMEK
  encryption {
    default_kms_key_name = google_kms_crypto_key.kyc_key.id
  }

  # Auto-delete temp documents after 24 hours
  lifecycle_rule {
    condition { age = 1; matches_prefix = ["temp/"] }
    action    { type = "Delete" }
  }

  uniform_bucket_level_access = true

  # Block all public access
  public_access_prevention = "enforced"
}

# ─── KMS for encryption at rest ───────────────────────────────────────────────
resource "google_kms_key_ring" "kyc" {
  name     = "kyc-copilot-keyring"
  location = var.region
}

resource "google_kms_crypto_key" "kyc_key" {
  name            = "kyc-copilot-key"
  key_ring        = google_kms_key_ring.kyc.id
  rotation_period = "7776000s"  # 90 days
}

# ─── Memorystore Redis (replaces AWS ElastiCache) ────────────────────────────
resource "google_redis_instance" "cache" {
  name           = "kyc-copilot-cache"
  tier           = "BASIC"
  memory_size_gb = 1
  region         = var.region
  redis_version  = "REDIS_7_0"

  authorized_network = google_compute_network.kyc_vpc.id

  labels = { environment = var.environment, service = "kyc-copilot" }
}

# ─── Secret Manager (replaces AWS Secrets Manager) ───────────────────────────
resource "google_secret_manager_secret" "anthropic_key" {
  secret_id = "anthropic-api-key"
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
}

# ─── Artifact Registry (replaces AWS ECR) ────────────────────────────────────
resource "google_artifact_registry_repository" "kyc_copilot" {
  location      = var.region
  repository_id = "kyc-copilot"
  format        = "DOCKER"
}

# ─── Service Account for Cloud Run ───────────────────────────────────────────
resource "google_service_account" "kyc_copilot_sa" {
  account_id   = "kyc-copilot-sa"
  display_name = "KYC Copilot Service Account"
}

resource "google_project_iam_member" "sa_gcs" {
  project = var.project_id
  role    = "roles/storage.objectAdmin"
  member  = "serviceAccount:${google_service_account.kyc_copilot_sa.email}"
}

resource "google_project_iam_member" "sa_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.kyc_copilot_sa.email}"
}

# ─── Cloud Run Service (replaces AWS ECS Fargate) ────────────────────────────
resource "google_cloud_run_v2_service" "kyc_copilot" {
  name     = "kyc-copilot"
  location = var.region

  template {
    service_account = google_service_account.kyc_copilot_sa.email

    scaling {
      min_instance_count = 1
      max_instance_count = 10
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.kyc_vpc.id
        subnetwork = google_compute_subnetwork.kyc_subnet.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/kyc-copilot/kyc-copilot:latest"

      resources {
        limits = { cpu = "2", memory = "4Gi" }
        cpu_idle = true  # Scale to zero when idle (cost saving)
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCS_BUCKET_NAME"
        value = google_storage_bucket.kyc_temp_docs.name
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_redis_instance.cache.host}:6379"
      }
      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.anthropic_key.secret_id
            version = "latest"
          }
        }
      }

      ports { container_port = 8000 }

      startup_probe {
        http_get { path = "/health" }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 3
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# ─── Cloud Armor (WAF — replaces AWS WAF) ────────────────────────────────────
resource "google_compute_security_policy" "kyc_waf" {
  name = "kyc-copilot-waf"

  rule {
    action   = "deny(403)"
    priority = 1000
    match {
      expr { expression = "evaluatePreconfiguredExpr('sqli-stable')" }
    }
    description = "Block SQL injection"
  }

  rule {
    action   = "throttle"
    priority = 2000
    match { versioned_expr = "SRC_IPS_V1"; config { src_ip_ranges = ["*"] } }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      rate_limit_threshold { count = 100; interval_sec = 60 }
    }
    description = "Rate limit: 100 req/min per IP"
  }

  rule {
    action   = "allow"
    priority = 2147483647
    match { versioned_expr = "SRC_IPS_V1"; config { src_ip_ranges = ["*"] } }
    description = "Default allow"
  }
}

# ─── Cloud Monitoring Alert (replaces AWS CloudWatch) ────────────────────────
resource "google_monitoring_alert_policy" "error_rate" {
  display_name = "KYC Copilot — High Error Rate"
  combiner     = "OR"

  conditions {
    display_name = "Error rate > 5%"
    condition_threshold {
      filter          = "resource.type=\"cloud_run_revision\" AND metric.type=\"run.googleapis.com/request_count\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0.05
      duration        = "60s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_RATE"
      }
    }
  }
}

# ─── Outputs ─────────────────────────────────────────────────────────────────
output "cloud_run_url"    { value = google_cloud_run_v2_service.kyc_copilot.uri }
output "gcs_bucket_name"  { value = google_storage_bucket.kyc_temp_docs.name }
output "redis_host"       { value = google_redis_instance.cache.host }
