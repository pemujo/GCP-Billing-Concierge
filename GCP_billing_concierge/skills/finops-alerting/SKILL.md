---
name: finops-alerting
description: Best practices and workflows for setting up Cloud Monitoring alert policies and notification channels for GCP billing anomalies and cost thresholds.
---

# FinOps Alerting Skill

This skill provides operational guidelines for creating and managing Cloud Monitoring alert policies and notification channels for GCP FinOps.

## Workflow
1. **Check & Inspect Existing Resources**:
   - Always call `list_channels` and `list_policies` to review existing channels, attached policies, and avoid duplicates.
   - Use `list_policies` or `get_policy` to verify whether an alert policy has notification channels attached, inspect condition log filters, and check enabled status.
2. **Notification Channel Creation & Verification**:
   - Types supported: `email`, `slack`, `pagerduty`.
   - Ensure the recipient address or webhook URL is validated before calling `setup_notification`.
   - **Email Verification**: Google Cloud Monitoring sends a verification email to new email channels. The channel remains `UNVERIFIED` until the recipient clicks the verification link in their email inbox. Alerts cannot be delivered to unverified channels.
3. **Alert Policy Creation & Channel Binding**:
   - Tie alert policies to Log-based metrics or Cloud Monitoring billing filters (such as anomalies logged by `log_billing_anomaly`).
   - Call `setup_alert_policy` with the channel IDs. If the policy already exists, `setup_alert_policy` will safely attach any missing channels in-place without creating duplicate policies.
   - Use descriptive display names indicating the severity and condition (e.g., `[FinOps Warning] Unexpected Spike in Compute Engine Cost`).
4. **Resource Management**:
   - Use `delete_resource` only when explicitly requested by the user to decommission retired alert policies or channels.

## Confidentiality and Data Protection
- Never disclose internal GCP project IDs, project numbers, raw table paths, or Secret Manager paths to the user.
- Refer to notification channels by their recipient email address and alert policies by their display name.
