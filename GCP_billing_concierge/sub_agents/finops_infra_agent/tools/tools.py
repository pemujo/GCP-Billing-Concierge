import json
from typing import Any, Dict, List

from google.api_core import exceptions
from google.cloud import (
    monitoring_v3,
    resourcemanager_v3,
    scheduler_v1,
    secretmanager,
)

# --- 1. Helper functions ---

def get_agent_id_from_secrets(project_id: str) -> str:
    """
    Fetches the latest Agent ID from Secret Manager.

    Args:
        project_id (str): The GCP Project ID where the secret is stored.

    Returns:
        str: The decoded Agent ID string from the 'latest' secret version.
    """
    client = secretmanager.SecretManagerServiceClient()
    # The 'latest' alias always points to the most recent deployment
    name = f"projects/{project_id}/secrets/finops-agent-id/versions/latest"

    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


def get_project_number(project_id: str) -> str:
    """
    Retrieves the numeric Project ID required for default service account construction.

    Args:
        project_id (str): The alphanumeric GCP Project ID.

    Returns:
        str: The numeric project identifier as a string.
    """
    client = resourcemanager_v3.ProjectsClient()
    res = client.get_project(name=f"projects/{project_id}")
    return res.name.split("/")[-1]


# --- 2. LISTING TOOLS  ---


def list_active_schedulers(project_id: str, region: str) -> List[Dict[str, str]]:
    """
    Lists all Cloud Scheduler jobs in a specific region.

    Args:
        project_id (str): The GCP Project ID.
        region (str): The GCP region (e.g., 'us-central1').

    Returns:
        List[Dict[str, str]]: A list of dictionaries containing job name, 
                              cron schedule, and current state.
    """
    client = scheduler_v1.CloudSchedulerClient()
    parent = f"projects/{project_id}/locations/{region}"
    jobs = client.list_jobs(parent=parent)
    return [
        {"name": j.name, "schedule": j.schedule, "state": j.state.name}
        for j in jobs
    ]


def list_notification_channels(project_id: str) -> List[Dict[str, Any]]:
    """
    Lists all configured monitoring notification channels.

    Args:
        project_id (str): The GCP Project ID.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing channel 
                               display name, type, ID, and email address.
    """
    client = monitoring_v3.NotificationChannelServiceClient()
    project_name = f"projects/{project_id}"
    channels = client.list_notification_channels(name=project_name)
    return [
        {
            "display_name": c.display_name,
            "type": c.type,
            "id": c.name,
            "email": c.labels.get("email_address"),
        }
        for c in channels
    ]


def list_alert_policies(project_id: str) -> List[Dict[str, Any]]:
    """
    Lists all active monitoring alert policies.

    Args:
        project_id (str): The GCP Project ID.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing policy 
                               display name, enabled status, and ID.
    """
    client = monitoring_v3.AlertPolicyServiceClient()
    project_name = f"projects/{project_id}"
    policies = client.list_alert_policies(name=project_name)
    return [
        {"display_name": p.display_name, "enabled": p.enabled, "id": p.name}
        for p in policies
    ]


# --- 3. CREATING TOOLS  ---


def create_billing_notification_channel(
    project_id: str, email_address: str
) -> str:
    """
    Creates an email notification channel. Checks for duplicates first.

    Args:
        project_id (str): The GCP Project ID.
        email_address (str): The email address to receive alerts.

    Returns:
        str: A status message indicating success, skip (if already exists), or error.
    """
    client = monitoring_v3.NotificationChannelServiceClient()
    project_name = f"projects/{project_id}"

    # Duplicate check to prevent spamming channels
    existing = list_notification_channels(project_id)
    for channel in existing:
        if channel["email"] == email_address:
            return f"""** SKIP ** 
            Notification channel for {email_address} already exists ({channel['id']})."""

    try:
        channel_data = {
            "display_name": f"FinOps Alert: {email_address}",
            "type": "email",
            "labels": {"email_address": email_address},
        }
        response = client.create_notification_channel(
            name=project_name, notification_channel=channel_data
        )
        return f"SUCCESS: Created channel {response.name}"
    except Exception as e:
        return f"ERROR: Failed to create channel: {str(e)}"


def create_billing_alert_policy(project_id: str, channel_ids: List[str]) -> str:
    """
    Creates a log-based alert policy and links it to provided channel IDs.

    Args:
        project_id (str): The GCP Project ID.
        channel_ids (List[str]): Full resource names of notification channels.

    Returns:
        str: A status message indicating success, skip, or error.
    """
    client = monitoring_v3.AlertPolicyServiceClient()
    project_name = f"projects/{project_id}"

    # Duplicate Check
    existing = list_alert_policies(project_id)
    if any(p["display_name"] == "billing-anomaly-detector" for p in existing):
        return "SKIP: Alert policy 'billing-anomaly-detector' already exists."

    alert_policy = {
        "display_name": "billing-anomaly-detector",
        "combiner": monitoring_v3.AlertPolicy.ConditionCombinerType.OR,
        "conditions": [
            {
                "display_name": "Log match: billing-anomaly-detector",
                "condition_matched_log": {
                    "filter": f'logName="projects/{project_id}/logs/billing-anomaly-detector"',
                },
            }
        ],
        "notification_channels": channel_ids,
        "alert_strategy": {
            "notification_rate_limit": {"period": {"seconds": 300}},
            "auto_close": {"seconds": 604800},
        },
    }
    try:
        response = client.create_alert_policy(
            name=project_name, alert_policy=alert_policy
        )
        return f"SUCCESS: Created Alert Policy {response.name}"
    except Exception as e:
        return f"ERROR: Failed to create alert policy: {str(e)}"


def create_scheduler(
    project_id: str, region: str, agent_full_id: str, schedule: str, description: str
) -> str:
    """
    Schedules or updates the Cloud Scheduler to trigger Agent in Agent Engine.

    Args:
        project_id (str): The GCP Project ID.
        region (str): The region (e.g., 'us-central1').
        agent_full_id (str): The full resource name of the Reasoning Engine.
        schedule (str): A cron expression (e.g., "0 9 * * *" for daily).
        description (str): A two word description of the scheduled job 
                           (e.g. monthly-audit, daily-audit)

    Returns:
        str: A status message indicating success (created or updated) or error.
    """
    client = scheduler_v1.CloudSchedulerClient()
    parent = f"projects/{project_id}/locations/{region}"
    job_name = f"{parent}/jobs/billing-concierge-{description}"

    project_num = get_project_number(project_id)

    compute_sa = f"{project_num}-compute@developer.gserviceaccount.com"

    job = {
        "name": job_name,
        "schedule": schedule,  # Dynamic cron string passed by the Agent
        "http_target": {
            "uri": f"https://{region}-aiplatform.googleapis.com/v1/{agent_full_id}:streamQuery",
            "http_method": scheduler_v1.HttpMethod.POST,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "class_method": "async_stream_query",
                    "input": {
                        "user_id": "scheduler_bot",
                        "message": f"Run billing audit for schedule: {schedule}",
                    },
                }
            ).encode("utf-8"),
            "oauth_token": {
                "service_account_email": compute_sa,
                "scope": "https://www.googleapis.com/auth/cloud-platform",
            },
        },
    }

    try:
        # Try to create the job first
        client.create_job(parent=parent, job=job)
        return f"SUCCESS: Created scheduler job with schedule '{schedule}'"
    except exceptions.AlreadyExists:
        # If it exists, update it so the new schedule takes effect
        update_mask = {"paths": ["schedule", "http_target"]}
        client.update_job(job=job, update_mask=update_mask)
        return f"SUCCESS: Updated existing job to new schedule '{schedule}'"
    except Exception as e:
        return f"ERROR: Failed to schedule audit: {str(e)}"


# --- 4. DELETE TOOLS  ---


def delete_finops_resource(resource_name: str, resource_type: str) -> str:
    """
    Deletes a specific GCP resource based on its type.

    Args:
        resource_name (str): The full resource identifier/name.
        resource_type (str): The type of resource ('scheduler', 'channel', or 'policy').

    Returns:
        str: A status message indicating success, skip (if not found), or error.
    """
    try:
        if resource_type == "scheduler":
            client = scheduler_v1.CloudSchedulerClient()
            client.delete_job(name=resource_name)
        elif resource_type == "channel":
            client = monitoring_v3.NotificationChannelServiceClient()
            client.delete_notification_channel(name=resource_name, force=True)
        elif resource_type == "policy":
            client = monitoring_v3.AlertPolicyServiceClient()
            client.delete_alert_policy(name=resource_name)
        else:
            return f"ERROR: Unknown resource type '{resource_type}'"

        return f"SUCCESS: Deleted {resource_type}: {resource_name}"
    except exceptions.NotFound:
        return f"SKIP: Resource {resource_name} not found."
    except Exception as e:
        return f"ERROR: Failed to delete {resource_name}: {str(e)}"