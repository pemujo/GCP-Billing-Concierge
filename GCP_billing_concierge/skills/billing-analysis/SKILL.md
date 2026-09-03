---
name: billing-analysis
description: Instructions, SQL query patterns, and FinOps guidelines for analyzing GCP billing export datasets in BigQuery, detecting cost anomalies, and performing Month-over-Month comparisons.
---

# GCP Billing Analysis Skill

This skill guides the FinOps agent on how to inspect, query, and analyze Google Cloud Billing exports in BigQuery.

## BigQuery Data Source Context
- Always refer to the billing data source generically as **"the billing export"** (never disclose internal project IDs or raw dataset names to the user).
- **48-Hour Synchronization Delay**: Google Cloud Billing export data has an inherent 48-hour pipeline delay.
  - **Rule**: Never query or analyze data from the current day ($T$) or yesterday ($T-1$), as records will be incomplete.
  - For "last week" or "recent days", set the upper date boundary to at least 2 days prior to today.

## Schema & Query Formulation Best Practices
1. **Always Inspect Schema First**:
   - Call `get_table_info` to verify column names and partitions before generating SQL.
2. **Mandatory Partition Filtering**:
   - Always filter by the partition column (typically `_PARTITIONDATE` or `DATE(usage_start_time)` / `DATE(export_time)`) to prevent full table scans.
3. **Never `SELECT *`**:
   - Only select the specific columns needed: `usage_start_time`, `service.description`, `sku.description`, `project.id`, `cost`, `credits`.
4. **Accurate Net Cost Calculation**:
   - The `cost` field reflects gross cost before discounts/credits.
   - To compute true net cost, UNNEST credits:
     ```sql
     cost + COALESCE((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0) AS net_cost
     ```
5. **Dry Run Guardrail**:
   - Always perform a **Dry Run** check before query execution.
   - If estimated bytes billed exceed 1 GB, stop and request user confirmation before running.

## Cost Anomaly & Trend Detection
1. **Anomaly Criteria**:
   - A service or SKU whose spend in the recent evaluation window is **>20% higher** than the baseline 30-day daily moving average.
   - Sudden appearance of a new high-cost SKU or region that had zero spend in prior weeks.
2. **Action on Anomaly**:
   - Summarize the top contributing factors (specific SKU, region, or project ID).
   - Use `log_billing_anomaly` to record the finding in Cloud Logging so that alert policies can notify the FinOps team.
