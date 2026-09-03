---
name: finops-alerting
description: Best practices and workflows for setting up Cloud Monitoring alert policies and notification channels for GCP billing anomalies and cost thresholds.
---

# FinOps Alerting Skill

This skill provides operational guidelines for creating and managing Cloud Monitoring alert policies and notification channels for GCP FinOps.

## Workflow
1. **Check Existing Resources**:
   - Always call `list_channels` and `list_policies` to avoid creating duplicate channels or overlapping policies.
2. **Notification Channel Creation**:
   - Types supported: `email`, `slack`, `pagerduty`.
   - Ensure the recipient address or webhook URL is validated before calling `setup_notification`.
3. **Alert Policy Creation**:
   - Tie alert policies to Log-based metrics or Cloud Monitoring billing filters (such as anomalies logged by `log_billing_anomaly`).
   - Use descriptive display names indicating the severity and condition (e.g., `[FinOps Warning] Unexpected Spike in Compute Engine Cost`).
4. **Resource Management**:
   - Use `delete_resource` only when explicitly requested by the user to decommission retired alert policies or channels.
