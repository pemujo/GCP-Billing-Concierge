# 1. THE PROJECT IDENTITY
variable "project_id" {
  type        = string
  description = "The GCP Project ID where resources will be deployed."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "The GCP region for the Scheduler and Agent Runtime."
}

# 2. THE WORKFLOW TOGGLE
variable "agent_deployed" {
  type        = bool
  default     = false
  description = "Toggle to 'true' ONLY after running 'make deploy' to set up automation."
}

# 3. THE NOTIFICATION TARGET
variable "alert_email" {
  type        = string
  description = "The email address that will receive billing anomaly alerts."
}