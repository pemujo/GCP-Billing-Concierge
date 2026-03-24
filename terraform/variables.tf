variable "project_id" {
  type        = string
  description = "The Google Cloud Project ID"
}

variable "alert_email" {
  type        = string
  description = "The email address for anomaly alerts"
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "The default region for resources"
}

variable "agent_id" {
  type        = string
  default     = "7928165481276506112"
  description = "The Reasoning Engine ID for the FinOps Agent"
}