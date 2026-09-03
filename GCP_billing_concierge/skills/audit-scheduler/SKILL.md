---
name: audit-scheduler
description: Instructions for configuring Cloud Scheduler jobs to execute recurring FinOps audits against the deployed Agent Runtime endpoint.
---

# Cloud Scheduler Audit Skill

This skill guides the agent in setting up automated, periodic FinOps audits using Google Cloud Scheduler.

## Schedule Configuration & CRON Mapping
Accurately translate user schedules into standard 5-field CRON expressions:
- **Weekly on Monday morning**: `0 9 * * 1`
- **Daily at midnight**: `0 0 * * *`
- **End of month (last day)**: `0 9 28-31 * *`
- **Bi-weekly**: `0 9 1,15 * *`

## Target & Authentication
- **Endpoint**: The deployed Vertex AI Agent Runtime streaming endpoint.
- **Payload**: Standard JSON payload with the query prompt for the audit:
  ```json
  {
    "message": "Run periodic FinOps billing anomaly scan and verify cost trends for the past 7 days."
  }
  ```
- **Service Account**: Cloud Scheduler must use the provisioned Agent Service Account with `roles/aiplatform.user` permission to authenticate via OIDC.
- **Verification**: Call `list_schedulers` to verify successful job registration and inspect `schedule` and `state`.
