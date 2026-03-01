# KYC Review Agent — GCP Infrastructure (Terraform)
# Region: northamerica-northeast1 (Montreal) for Canadian data residency

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 5.0" }
  }
  backend "gcs" {
    bucket = "kyc-terraform-state"
    prefix = "kyc-review-agent"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id"  { description = "GCP Project ID" }
variable "region"      { default = "northamerica-northeast1" }
variable "environment" { default = "production" }

# ─── APIs ─────────────────────────────────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "storage.googleapis.com",
    "redis.googleapis.com",
    "pubsub.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])
  service            = each.key
  disable_on_destroy = false
}

# ─── VPC ──────────────────────────────────────────────────────────────────────
resource "google_compute_network" "kyc_vpc" {
  name                    = "kyc-review-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "kyc_subnet" {
  name                     = "kyc-review-subnet"
  ip_cidr_range            = "10.0.2.0/24"
  region                   = var.region
  network                  = google_compute_network.kyc_vpc.id
  private_ip_google_access = true
}

# ─── KMS ──────────────────────────────────────────────────────────────────────
resource "google_kms_key_ring" "kyc_review" {
  name     = "kyc-review-keyring"
  location = var.region
}

resource "google_kms_crypto_key" "kyc_review_key" {
  name            = "kyc-review-key"
  key_ring        = google_kms_key_ring.kyc_review.id
  rotation_period = "7776000s"
}

# ─── GCS Bucket (replaces AWS S3) ─────────────────────────────────────────────
resource "google_storage_bucket" "kyc_review_docs" {
  name          = "kyc-review-documents-${var.project_id}"
  location      = "NORTHAMERICA-NORTHEAST1"
  force_destroy = false

  encryption {
    default_kms_key_name = google_kms_crypto_key.kyc_review_key.id
  }

  # Temp documents — deleted after 24h
  lifecycle_rule {
    condition { age = 1; matches_prefix = ["temp/"] }
    action    { type = "Delete" }
  }

  # Reviewed documents — retained 90 days (FINTRAC: extend to 5 years in prod)
  lifecycle_rule {
    condition { age = 90; matches_prefix = ["reviewed/"] }
    action    { type = "Delete" }
  }

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
}

# ─── Cloud SQL PostgreSQL (replaces AWS RDS) ──────────────────────────────────
resource "google_sql_database_instance" "kyc_audit" {
  name             = "kyc-review-audit-${var.environment}"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier              = "db-g1-small"
    availability_type = "REGIONAL"  # Multi-zone HA

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      backup_retention_settings {
        retained_backups = 30
      }
    }

    ip_configuration {
      ipv4_enabled                                  = false  # Private only
      private_network                               = google_compute_network.kyc_vpc.id
      enable_private_path_for_google_cloud_services = true
    }

    database_flags {
      name  = "log_connections"
      value = "on"
    }
  }

  deletion_protection = true
}

resource "google_sql_database" "kyc_review_db" {
  name     = "kyc_review"
  instance = google_sql_database_instance.kyc_audit.name
}

resource "google_sql_user" "kyc_app_user" {
  name     = "kyc_app"
  instance = google_sql_database_instance.kyc_audit.name
  password = random_password.db_password.result
}

resource "random_password" "db_password" {
  length  = 32
  special = true
}

# ─── Memorystore Redis (replaces AWS ElastiCache) ────────────────────────────
resource "google_redis_instance" "review_cache" {
  name           = "kyc-review-cache"
  tier           = "STANDARD_HA"   # High availability
  memory_size_gb = 2
  region         = var.region
  redis_version  = "REDIS_7_0"

  authorized_network = google_compute_network.kyc_vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"
}

# ─── Pub/Sub (replaces AWS SQS + SNS) ────────────────────────────────────────
# Main review queue topic
resource "google_pubsub_topic" "review_submissions" {
  name = "kyc-review-submissions"

  message_storage_policy {
    allowed_persistence_regions = [var.region]
  }
}

# Subscription for the review worker (Cloud Run)
resource "google_pubsub_subscription" "review_worker" {
  name  = "kyc-review-worker-sub"
  topic = google_pubsub_topic.review_submissions.name

  ack_deadline_seconds       = 300
  message_retention_duration = "86400s"  # 24h

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.review_dlq.id
    max_delivery_attempts = 3
  }
}

# Dead-letter queue topic (replaces AWS SQS DLQ)
resource "google_pubsub_topic" "review_dlq" {
  name = "kyc-review-dlq"
}

# Fraud alerts topic (replaces AWS SNS)
resource "google_pubsub_topic" "fraud_alerts" {
  name = "kyc-fraud-alerts"
}

# Email subscription for fraud alerts (via SendGrid/Mailgun push endpoint)
resource "google_pubsub_subscription" "fraud_email_push" {
  name  = "kyc-fraud-email-push"
  topic = google_pubsub_topic.fraud_alerts.name

  push_config {
    push_endpoint = "https://hooks.example.com/kyc-fraud-alert"
    oidc_token {
      service_account_email = google_service_account.review_agent_sa.email
    }
  }
}

# ─── Secret Manager ───────────────────────────────────────────────────────────
resource "google_secret_manager_secret" "anthropic_key" {
  secret_id = "anthropic-api-key"
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "kyc-db-password"
  replication {
    user_managed {
      replicas { location = var.region }
    }
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}

# ─── Service Account for Cloud Run ───────────────────────────────────────────
resource "google_service_account" "review_agent_sa" {
  account_id   = "kyc-review-agent-sa"
  display_name = "KYC Review Agent Service Account"
}

resource "google_project_iam_member" "sa_gcs"     { project = var.project_id; role = "roles/storage.objectAdmin";         member = "serviceAccount:${google_service_account.review_agent_sa.email}" }
resource "google_project_iam_member" "sa_pubsub"  { project = var.project_id; role = "roles/pubsub.editor";               member = "serviceAccount:${google_service_account.review_agent_sa.email}" }
resource "google_project_iam_member" "sa_secrets" { project = var.project_id; role = "roles/secretmanager.secretAccessor"; member = "serviceAccount:${google_service_account.review_agent_sa.email}" }
resource "google_project_iam_member" "sa_sql"     { project = var.project_id; role = "roles/cloudsql.client";              member = "serviceAccount:${google_service_account.review_agent_sa.email}" }
resource "google_project_iam_member" "sa_logging" { project = var.project_id; role = "roles/logging.logWriter";           member = "serviceAccount:${google_service_account.review_agent_sa.email}" }

# ─── Artifact Registry ────────────────────────────────────────────────────────
resource "google_artifact_registry_repository" "review_agent" {
  location      = var.region
  repository_id = "kyc-review-agent"
  format        = "DOCKER"
}

# ─── Cloud Run Service (replaces AWS ECS Fargate) ────────────────────────────
resource "google_cloud_run_v2_service" "kyc_review_agent" {
  name     = "kyc-review-agent"
  location = var.region

  template {
    service_account = google_service_account.review_agent_sa.email

    scaling {
      min_instance_count = 1
      max_instance_count = 20
    }

    vpc_access {
      network_interfaces {
        network    = google_compute_network.kyc_vpc.id
        subnetwork = google_compute_subnetwork.kyc_subnet.id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    # Cloud SQL connection
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.kyc_audit.connection_name]
      }
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/kyc-review-agent/kyc-review-agent:latest"

      resources {
        limits   = { cpu = "4", memory = "8Gi" }
        cpu_idle = false  # Always-on CPU for compliance SLA
      }

      env { name = "GCP_PROJECT_ID";       value = var.project_id }
      env { name = "GCS_BUCKET_NAME";      value = google_storage_bucket.kyc_review_docs.name }
      env { name = "PUBSUB_TOPIC_ID";      value = google_pubsub_topic.review_submissions.name }
      env { name = "PUBSUB_FRAUD_TOPIC_ID";value = google_pubsub_topic.fraud_alerts.name }
      env { name = "REDIS_URL";            value = "redis://${google_redis_instance.review_cache.host}:6379" }
      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref { secret = google_secret_manager_secret.anthropic_key.secret_id; version = "latest" }
        }
      }
      env {
        name = "DATABASE_PASSWORD"
        value_source {
          secret_key_ref { secret = google_secret_manager_secret.db_password.secret_id; version = "latest" }
        }
      }

      volume_mounts { name = "cloudsql"; mount_path = "/cloudsql" }

      ports { container_port = 8001 }

      startup_probe {
        http_get { path = "/health" }
        initial_delay_seconds = 10
        period_seconds        = 5
        failure_threshold     = 3
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# ─── Cloud Monitoring Alerts ──────────────────────────────────────────────────
resource "google_monitoring_alert_policy" "fraud_flag_alert" {
  display_name = "KYC — Fraud Flag Detected"
  combiner     = "OR"

  conditions {
    display_name = "Fraud flag log entry"
    condition_matched_log {
      filter = "jsonPayload.decision=\"fraud_flag\""
    }
  }

  notification_channels = []  # Add PagerDuty/email channel IDs here
}

resource "google_monitoring_alert_policy" "dlq_alert" {
  display_name = "KYC — Dead Letter Queue Growing"
  combiner     = "OR"

  conditions {
    display_name = "DLQ message count > 5"
    condition_threshold {
      filter          = "resource.type=\"pubsub_subscription\" AND resource.label.subscription_id=\"kyc-review-dlq\""
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"
    }
  }
}

# ─── Cloud Logging Sink (audit log export) ───────────────────────────────────
resource "google_logging_project_sink" "kyc_audit_sink" {
  name        = "kyc-review-audit-sink"
  destination = "storage.googleapis.com/${google_storage_bucket.kyc_review_docs.name}"
  filter      = "resource.type=\"cloud_run_revision\" AND jsonPayload.service=\"kyc-review-agent\""

  unique_writer_identity = true
}

# ─── Outputs ──────────────────────────────────────────────────────────────────
output "cloud_run_url"          { value = google_cloud_run_v2_service.kyc_review_agent.uri }
output "gcs_bucket_name"        { value = google_storage_bucket.kyc_review_docs.name }
output "redis_host"             { value = google_redis_instance.review_cache.host }
output "cloud_sql_connection"   { value = google_sql_database_instance.kyc_audit.connection_name }
output "pubsub_topic"           { value = google_pubsub_topic.review_submissions.name }
output "artifact_registry_repo" { value = google_artifact_registry_repository.review_agent.name }
