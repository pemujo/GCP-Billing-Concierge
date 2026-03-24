terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0" 
    }
  }
  backend "gcs" {
    bucket = "opm-tests-tfstate"
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# --- DATA SOURCES ---
data "google_project" "project" {
  project_id = var.project_id
}

data "google_compute_default_service_account" "default" {
  project = var.project_id
}

# --- MONITORING ---

# 1. Define the Channel
resource "google_monitoring_notification_channel" "email_billing_alerts" {
  project      = var.project_id
  display_name = "Billing Anomaly Email Channel"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

# 2. Define the Policy and link it to the Channel above
resource "google_monitoring_alert_policy" "billing_anomaly_alert" {
  project      = var.project_id
  display_name = "billing-anomaly-detector"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "Log match condition"
    condition_matched_log {
      filter = "logName=\"projects/${var.project_id}/logs/billing-anomaly-detector\""
    }
  }

  # REQUIRED for log-based alerts
  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "604800s"
  }

  notification_channels = [
    google_monitoring_notification_channel.email_billing_alerts.name
  ]
}

# --- SCHEDULER & IAM ---

resource "google_service_account_iam_member" "scheduler_impersonate" {
  service_account_id = data.google_compute_default_service_account.default.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
}

resource "google_cloud_scheduler_job" "finops_agent_trigger" {
  name             = "finops-10min-anomaly-check"
  schedule         = "0 * * * *"
  region           = var.region
  project          = var.project_id

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-aiplatform.googleapis.com/v1/projects/${var.project_id}/locations/${var.region}/reasoningEngines/${var.agent_id}:streamQuery"
    
    body = base64encode(jsonencode({
      class_method = "async_stream_query"
      input = {
        user_id = "u_123"
        message = "is there an anomaly in my GCP billing the last week of February 2026 compared to the previous two months?"
      }
    }))

    headers = { "Content-Type" = "application/json" }

    oauth_token {
      service_account_email = data.google_compute_default_service_account.default.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_service_account_iam_member.scheduler_impersonate]
}