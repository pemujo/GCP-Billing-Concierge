import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import tzlocal
from google.api_core import exceptions
from google.cloud import (
    monitoring_v3,
    scheduler_v1,
    secretmanager,
)

logger = logging.getLogger(__name__)

# --- 1. Helper functions ---


def get_resolved_project_id(project_id: Optional[str] = None) -> str:
    """Dynamically resolves the alphanumeric GCP Project ID.

    Addresses b/502326852 where os.getenv("GOOGLE_CLOUD_PROJECT") returns
    the numeric project number at module import time in Agent Engine, but
    returns the alphanumeric project ID at tool execution time (runtime).
    """
    runtime_project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if runtime_project and not runtime_project.isdigit():
        return runtime_project

    given = (project_id or "").strip()
    if given and not given.isdigit():
        return given

    for env_var in ["BILLING_EXPORT_PROJECT_ID", "PROJECT_ID", "GCP_PROJECT"]:
        val = os.getenv(env_var, "").strip()
        if val and not val.isdigit():
            return val

    candidate = runtime_project or given
    if candidate and candidate.isdigit():
        try:
            from google.cloud import resourcemanager_v3
            client = resourcemanager_v3.ProjectsClient()
            res = client.get_project(name=f"projects/{candidate}")
            if res.project_id:
                return res.project_id
        except Exception:
            pass

    return candidate


def get_agent_id_from_secrets(project_id: str) -> Optional[str]:
    """
    Fetches the Agent ID from environment variables, deployment metadata, or Secret Manager.

    Args:
        project_id (str): The GCP Project ID.

    Returns:
        Optional[str]: The secret value (Agent ID string) or None if not found/accessible.
    """
    project_id = get_resolved_project_id(project_id)
    # 1. Environment variable override (AGENT_ENGINE_ID or REASONING_ENGINE_ID)
    env_agent_id = os.getenv("AGENT_ENGINE_ID") or os.getenv("REASONING_ENGINE_ID")
    if env_agent_id:
        logger.info("Using Agent ID from environment override: %s", env_agent_id)
        return env_agent_id.strip()

    # 2. Local deployment metadata fallback
    for meta_path in ["deployment_metadata.json", "../deployment_metadata.json"]:
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    aid = (
                        meta.get("remote_agent_engine_id")
                        or meta.get("remote_agent_runtime_id")
                        or meta.get("reasoning_engine_id")
                        or meta.get("agent_id")
                    )
                    if aid:
                        logger.info("Using Agent ID from %s: %s", meta_path, aid)
                        return str(aid).strip()
            except Exception:
                pass

    # 3. Secret Manager lookup
    client = secretmanager.SecretManagerServiceClient()
    secret_name = "billing-concierge-agent-id"
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"

    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    except exceptions.NotFound:
        logger.warning(
            "Secret '%s' or version 'latest' not found in project %s.",
            secret_name,
            project_id,
        )
    except exceptions.PermissionDenied:
        logger.error(
            "Permission denied: Ensure the SA has 'Secret Manager Secret Accessor' on %s.",
            secret_name,
        )
    except exceptions.InvalidArgument:
        logger.error(
            "Invalid argument: Check if project_id '%s' is correct.", project_id
        )
    except Exception as e:
        logger.error("An unexpected error occurred while fetching secret: %s", e)

    return None


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
    project_id = get_resolved_project_id(project_id)
    if not region:
        region = (
            os.getenv("GOOGLE_CLOUD_REGION")
            or os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or "us-central1"
        )
    try:
        client = scheduler_v1.CloudSchedulerClient()
        parent = f"projects/{project_id}/locations/{region}"
        jobs = client.list_jobs(parent=parent)
        return [
            {
                "name": j.name.split("/")[-1],
                "schedule": j.schedule,
                "state": j.state.name,
            }
            for j in jobs
        ]
    except Exception as e:
        logger.error("Failed to list scheduler jobs: %s", e)
        return [{"error": "Failed to list scheduler jobs. Please check Cloud Scheduler permissions."}]


def list_notification_channels(project_id: str) -> List[Dict[str, Any]]:
    """
    Lists all configured monitoring notification channels.

    Args:
        project_id (str): The GCP Project ID.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing channel 
                               display name, type, ID, email address, verification status, and enabled status.
    """
    project_id = get_resolved_project_id(project_id)
    try:
        client = monitoring_v3.NotificationChannelServiceClient()
        project_name = f"projects/{project_id}"
        channels = client.list_notification_channels(name=project_name)
        return [
            {
                "display_name": c.display_name,
                "type": c.type,
                "id": c.name.split("/")[-1],
                "email": c.labels.get("email_address"),
                "verification_status": (
                    c.verification_status.name
                    if hasattr(c, "verification_status") and hasattr(c.verification_status, "name")
                    else str(getattr(c, "verification_status", "UNKNOWN"))
                ),
                "enabled": bool(getattr(c, "enabled", True)),
            }
            for c in channels
        ]
    except Exception as e:
        logger.error("Failed to list notification channels: %s", e)
        return [{"error": "Failed to list notification channels. Please check Cloud Monitoring permissions."}]


def list_alert_policies(project_id: str) -> List[Dict[str, Any]]:
    """
    Lists all active monitoring alert policies with full details.

    Args:
        project_id (str): The GCP Project ID.

    Returns:
        List[Dict[str, Any]]: A list of dictionaries containing policy 
                               display name, enabled status, ID, notification channels, and condition details.
    """
    project_id = get_resolved_project_id(project_id)
    try:
        client = monitoring_v3.AlertPolicyServiceClient()
        project_name = f"projects/{project_id}"
        policies = client.list_alert_policies(name=project_name)
        return [
            {
                "id": p.name.split("/")[-1],
                "display_name": p.display_name,
                "enabled": bool(p.enabled),
                "notification_channels": [
                    cid.split("/")[-1] for cid in p.notification_channels
                ],
                "conditions": [
                    {
                        "name": c.name.split("/")[-1],
                        "display_name": c.display_name,
                        "filter": (
                            re.sub(r'projects/[^/]+/logs/', 'logs/', c.condition_matched_log.filter)
                            if c.condition_matched_log
                            else (
                                re.sub(r'projects/[^/]+/logs/', 'logs/', c.condition_threshold.filter)
                                if c.condition_threshold
                                else ""
                            )
                        ),
                    }
                    for c in p.conditions
                ],
            }
            for p in policies
        ]
    except Exception as e:
        logger.error("Failed to list alert policies: %s", e)
        return [{"error": "Failed to list alert policies. Please check Cloud Monitoring permissions."}]


def get_alert_policy(project_id: str, policy_id: str) -> Dict[str, Any]:
    """
    Gets detailed configuration for a specific alert policy.

    Args:
        project_id (str): The GCP Project ID.
        policy_id (str): The full resource name or ID of the alert policy.

    Returns:
        Dict[str, Any]: Full alert policy configuration attributes.
    """
    project_id = get_resolved_project_id(project_id)
    try:
        client = monitoring_v3.AlertPolicyServiceClient()
        name = (
            policy_id
            if policy_id.startswith("projects/")
            else f"projects/{project_id}/alertPolicies/{policy_id}"
        )
        p = client.get_alert_policy(name=name)
        return {
            "id": p.name.split("/")[-1],
            "display_name": p.display_name,
            "enabled": bool(p.enabled),
            "notification_channels": [
                cid.split("/")[-1] for cid in p.notification_channels
            ],
            "conditions": [
                {
                    "name": c.name.split("/")[-1],
                    "display_name": c.display_name,
                    "filter": (
                        re.sub(r'projects/[^/]+/logs/', 'logs/', c.condition_matched_log.filter)
                        if c.condition_matched_log
                        else (
                            re.sub(r'projects/[^/]+/logs/', 'logs/', c.condition_threshold.filter)
                            if c.condition_threshold
                            else ""
                        )
                    ),
                }
                for c in p.conditions
            ],
            "combiner": (
                p.combiner.name if hasattr(p.combiner, "name") else str(p.combiner)
            ),
        }
    except Exception as e:
        logger.error("Failed to get alert policy %s: %s", policy_id, e)
        return {"error": f"Failed to get alert policy '{policy_id.split('/')[-1]}'."}


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
        str: A status message indicating success, exists (if already exists), or error.
    """
    project_id = get_resolved_project_id(project_id)
    client = monitoring_v3.NotificationChannelServiceClient()
    project_name = f"projects/{project_id}"

    # Duplicate check to prevent spamming channels
    existing = list_notification_channels(project_id)
    for channel in existing:
        if channel.get("email") == email_address:
            v_status = channel.get("verification_status", "UNKNOWN")
            msg = (
                f"EXISTS: Notification channel for {email_address} already exists "
                f"({channel.get('id')}). Verification status: {v_status}."
            )
            if v_status == "UNVERIFIED":
                msg += (
                    " IMPORTANT: Google Cloud Monitoring requires clicking the verification "
                    "link sent to this email before alert notifications can be delivered."
                )
            return msg

    try:
        channel_data = {
            "display_name": f"FinOps Alert: {email_address}",
            "type": "email",
            "labels": {"email_address": email_address},
        }
        response = client.create_notification_channel(
            name=project_name, notification_channel=channel_data
        )
        channel_id = response.name.split("/")[-1]
        return (
            f"SUCCESS: Created notification channel (ID: {channel_id}) for {email_address}. "
            "NOTE: Google Cloud Monitoring sends a verification email to this address. "
            "The recipient must click the verification link before alert emails will arrive."
        )
    except Exception as e:
        logger.error("Failed to create notification channel: %s", e)
        return "ERROR: Failed to create notification channel. Please check Cloud Monitoring permissions."


def create_billing_alert_policy(project_id: str, channel_ids: List[str]) -> str:
    """
    Creates or updates a log-based alert policy and links it to provided channel IDs.

    If the policy already exists, this function safely attaches any missing channel IDs
    and ensures the policy is enabled and using the robust log filter.

    Args:
        project_id (str): The GCP Project ID.
        channel_ids (List[str]): Full resource names or short IDs of notification channels.

    Returns:
        str: A status message indicating success, updated, or error.
    """
    project_id = get_resolved_project_id(project_id)
    client = monitoring_v3.AlertPolicyServiceClient()
    project_name = f"projects/{project_id}"
    robust_filter = (
        f'logName="projects/{project_id}/logs/billing-anomaly-detector" OR '
        f'log_id("billing-anomaly-detector")'
    )

    formatted_channel_ids = [
        cid if cid.startswith("projects/") else f"projects/{project_id}/notificationChannels/{cid}"
        for cid in channel_ids
    ]

    # Duplicate Check & In-place update
    existing = list_alert_policies(project_id)
    matching_policy = next(
        (p for p in existing if p.get("display_name") == "billing-anomaly-detector"),
        None,
    )

    if matching_policy:
        policy_id = matching_policy.get("id")
        try:
            full_policy_name = (
                policy_id
                if policy_id.startswith("projects/")
                else f"projects/{project_id}/alertPolicies/{policy_id}"
            )
            policy_obj = client.get_alert_policy(name=full_policy_name)
            current_channels = list(policy_obj.notification_channels)
            channels_to_add = [
                cid for cid in formatted_channel_ids if cid not in current_channels
            ]

            needs_update = False
            update_fields = []

            if channels_to_add:
                policy_obj.notification_channels.extend(channels_to_add)
                update_fields.append("notification_channels")
                needs_update = True

            if not policy_obj.enabled:
                policy_obj.enabled = True
                update_fields.append("enabled")
                needs_update = True

            if needs_update:
                from google.protobuf import field_mask_pb2

                mask = field_mask_pb2.FieldMask(paths=update_fields)
                updated = client.update_alert_policy(
                    alert_policy=policy_obj, update_mask=mask
                )
                policy_short_id = updated.name.split("/")[-1]
                return (
                    f"SUCCESS: Updated existing alert policy '{updated.display_name}' (ID: {policy_short_id}). "
                    "Attached configured notification channels."
                )
            else:
                policy_short_id = policy_id.split("/")[-1]
                return (
                    f"VERIFIED: Alert policy '{matching_policy.get('display_name')}' (ID: {policy_short_id}) is active "
                    "and already linked to configured notification channels."
                )
        except Exception as e:
            logger.error("Failed to update existing alert policy: %s", e)
            return "ERROR: Failed to update existing alert policy. Please check Cloud Monitoring permissions."

    alert_policy = {
        "display_name": "billing-anomaly-detector",
        "combiner": monitoring_v3.AlertPolicy.ConditionCombinerType.OR,
        "conditions": [
            {
                "display_name": "Log match: billing-anomaly-detector",
                "condition_matched_log": {
                    "filter": robust_filter,
                },
            }
        ],
        "notification_channels": formatted_channel_ids,
        "alert_strategy": {
            "notification_rate_limit": {"period": {"seconds": 300}},
            "auto_close": {"seconds": 604800},
        },
        "enabled": True,
    }
    try:
        response = client.create_alert_policy(
            name=project_name, alert_policy=alert_policy
        )
        policy_short_id = response.name.split("/")[-1]
        return (
            f"SUCCESS: Created alert policy '{alert_policy['display_name']}' (ID: {policy_short_id}) "
            "linked to configured notification channels."
        )
    except Exception as e:
        logger.error("Failed to create alert policy: %s", e)
        return "ERROR: Failed to create alert policy. Please check Cloud Monitoring permissions."


def create_scheduler(
    project_id: str, region: str, message: str, schedule: str, description: str
) -> str:
    """
    Schedules or updates the Cloud Scheduler to trigger Agent in Agent Engine.

    Args:
        project_id (str): The GCP Project ID.
        region (str): The region (e.g., 'us-central1').
        message (str): The prompt sent to the agent during scheduled runs.
        schedule (str): A cron expression (e.g., "0 9 * * *" for daily).
        description (str): A description ID of the scheduled job (e.g. monthly-audit, daily-audit).

    Returns:
        str: A status message indicating success (created or updated) or error.
    """
    project_id = get_resolved_project_id(project_id)
    agent_full_id = get_agent_id_from_secrets(project_id)
    if not agent_full_id:
        return (
            "ERROR: Could not retrieve Agent ID from Secret Manager. "
            "Ensure the agent is deployed and the agent ID secret is configured."
        )

    if not region:
        region = (
            os.getenv("GOOGLE_CLOUD_REGION")
            or os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or "us-central1"
        )

    client = scheduler_v1.CloudSchedulerClient()
    parent = f"projects/{project_id}/locations/{region}"
    job_name = f"{parent}/jobs/billing-concierge-{description}"

    local_tz = os.getenv("TIMEZONE", "")
    if not local_tz:
        try:
            local_tz = tzlocal.get_localzone_name()
        except Exception:
            local_tz = "UTC"

    service_account_id = "gcp-billing-concierge-sa"
    scheduler_sa = f"{service_account_id}@{project_id}.iam.gserviceaccount.com"

    logger.info(
        "Configuring scheduler job '%s' with SA %s, timezone: %s",
        job_name,
        scheduler_sa,
        local_tz,
    )

    # Ensure resource_path is fully-qualified
    if not agent_full_id.startswith("projects/"):
        resource_path = f"projects/{project_id}/locations/{region}/reasoningEngines/{agent_full_id}"
    else:
        resource_path = agent_full_id

    # Align endpoint location with the resource path location
    endpoint_location = region
    parts = resource_path.split("/")
    if "locations" in parts:
        loc_idx = parts.index("locations") + 1
        if loc_idx < len(parts):
            path_location = parts[loc_idx]
            if path_location != region:
                logger.warning(
                    "Agent resource location '%s' does not match regional infrastructure location '%s'. Using '%s'.",
                    path_location,
                    region,
                    path_location,
                )
            endpoint_location = path_location

    target_uri = f"https://{endpoint_location}-aiplatform.googleapis.com/v1/{resource_path}:streamQuery"

    job = {
        "name": job_name,
        "schedule": schedule,
        "time_zone": local_tz,
        "http_target": {
            "uri": target_uri,
            "http_method": scheduler_v1.HttpMethod.POST,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "class_method": "async_stream_query",
                    "input": {
                        "user_id": "billing_concierge_audit",
                        "message": message,
                    },
                }
            ).encode("utf-8"),
            "oauth_token": {
                "service_account_email": scheduler_sa,
                "scope": "https://www.googleapis.com/auth/cloud-platform",
            },
        },
    }

    logger.debug("Scheduler job configuration: %s", job)

    try:
        # Try to create the job first
        client.create_job(parent=parent, job=job)
        return f"SUCCESS: Created scheduler job '{description}' with schedule '{schedule}'."
    except exceptions.AlreadyExists:
        # If it exists, update it so the new schedule takes effect
        update_mask = {"paths": ["schedule", "http_target", "time_zone"]}
        client.update_job(job=job, update_mask=update_mask)
        return f"SUCCESS: Updated existing scheduler job '{description}' to schedule '{schedule}'."
    except Exception as e:
        logger.error("ERROR: Failed to schedule audit: %s", str(e))
        return "ERROR: Failed to schedule audit. Please check Cloud Scheduler permissions."


# --- 4. DELETE TOOLS  ---


def delete_finops_resource(resource_name: str, resource_type: str) -> str:
    """
    Deletes a specific GCP resource based on its type.

    Args:
        resource_name (str): The resource identifier or name.
        resource_type (str): The type of resource ('scheduler', 'channel', or 'policy').

    Returns:
        str: A status message indicating success, skip (if not found), or error.
    """
    short_name = resource_name.split("/")[-1]
    try:
        if resource_type == "scheduler":
            client = scheduler_v1.CloudSchedulerClient()
            target_name = (
                resource_name
                if resource_name.startswith("projects/")
                else f"projects/{get_resolved_project_id()}/locations/{os.getenv('GOOGLE_CLOUD_REGION', 'us-central1')}/jobs/{resource_name}"
            )
            client.delete_job(name=target_name)
        elif resource_type == "channel":
            client = monitoring_v3.NotificationChannelServiceClient()
            target_name = (
                resource_name
                if resource_name.startswith("projects/")
                else f"projects/{get_resolved_project_id()}/notificationChannels/{resource_name}"
            )
            client.delete_notification_channel(name=target_name, force=True)
        elif resource_type == "policy":
            client = monitoring_v3.AlertPolicyServiceClient()
            target_name = (
                resource_name
                if resource_name.startswith("projects/")
                else f"projects/{get_resolved_project_id()}/alertPolicies/{resource_name}"
            )
            client.delete_alert_policy(name=target_name)
        else:
            return f"ERROR: Unknown resource type '{resource_type}'. Must be 'scheduler', 'channel', or 'policy'."

        return f"SUCCESS: Deleted {resource_type} '{short_name}'."
    except exceptions.NotFound:
        return f"SKIP: Resource '{short_name}' not found."
    except Exception as e:
        logger.error("Failed to delete %s (%s): %s", resource_type, resource_name, e)
        return f"ERROR: Failed to delete {resource_type} '{short_name}'."
