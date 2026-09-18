"""FinOps Infrastructure Toolset for GCP Billing Concierge.

Encapsulates Cloud Scheduler, Cloud Monitoring (Alert Policies & Notification Channels),
and Secret Manager tools inside a standardized ADK BaseToolset.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Union

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools import BaseTool, FunctionTool
from google.adk.tools.base_toolset import BaseToolset, ToolPredicate
from google.api_core import exceptions
from google.cloud import (
    monitoring_v3,
    scheduler_v1,
    secretmanager,
)
import tzlocal

logger = logging.getLogger(__name__)


class FinOpsInfraToolset(BaseToolset):
    """Encapsulated toolset for managing Cloud Scheduler, Monitoring, and Secrets."""

    def __init__(
        self,
        project_id: str,
        region: Optional[str] = None,
        location: Optional[str] = None,
        credentials: Optional[Any] = None,
        timezone: Optional[str] = None,
        service_account_id: str = "gcp-billing-concierge-sa",
        agent_id_secret_name: Optional[str] = None,
        require_confirmation_for_delete: bool = True,
        *,
        tool_filter: Optional[Union[ToolPredicate, List[str]]] = None,
        tool_name_prefix: Optional[str] = None,
    ):
        """Initializes the FinOps Infrastructure Toolset.

        Args:
            project_id: The GCP Project ID where resources and secrets reside.
            region: The GCP region for regional resources like Cloud Scheduler.
            location: Alias for region for backward compatibility.
            credentials: Optional Google Auth credentials.
            timezone: Optional IANA timezone string for cron schedules.
            service_account_id: The SA ID prefix used by Cloud Scheduler to trigger the agent.
            agent_id_secret_name: Secret name storing the Reasoning Engine / Agent Runtime ID.
            require_confirmation_for_delete: Whether delete_resource requires user confirmation.
            tool_filter: Optional filter to expose a subset of tools.
            tool_name_prefix: Optional prefix for all tool names.
        """
        super().__init__(tool_filter=tool_filter, tool_name_prefix=tool_name_prefix)
        self._project_id = project_id
        self.region = (
            region
            or location
            or os.getenv("GOOGLE_CLOUD_REGION")
            or os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or "us-central1"
        )
        self.location = self.region
        self.credentials = credentials
        self.timezone = timezone
        self.service_account_id = service_account_id

        if not agent_id_secret_name:
            agent_id_secret_name = os.getenv("AGENT_ID_SECRET_NAME")
        if not agent_id_secret_name:
            agent_name_env = os.getenv("AGENT_NAME")
            if agent_name_env and agent_name_env.strip():
                clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", agent_name_env).lower().strip("-")
                if clean_name and clean_name not in ("gcp_billing_concierge", "gcp-billing-concierge"):
                    agent_id_secret_name = f"{clean_name}-agent-id"
        if not agent_id_secret_name:
            agent_id_secret_name = "billing-concierge-agent-id"
        self.agent_id_secret_name = agent_id_secret_name
        self.require_confirmation_for_delete = require_confirmation_for_delete

        # Lazily-cached client instances
        self._scheduler_client: Optional[scheduler_v1.CloudSchedulerClient] = None
        self._channel_client: Optional[monitoring_v3.NotificationChannelServiceClient] = None
        self._alert_policy_client: Optional[monitoring_v3.AlertPolicyServiceClient] = None
        self._secret_client: Optional[secretmanager.SecretManagerServiceClient] = None

    @property
    def project_id(self) -> str:
        """Dynamically resolves the alphanumeric GCP Project ID.

        Addresses b/502326852 where os.getenv("GOOGLE_CLOUD_PROJECT") returns
        the numeric project number at module import time in Agent Engine, but
        returns the alphanumeric project ID at tool execution time (runtime).
        """
        # 1. Prefer runtime environment variable if alphanumeric
        runtime_project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
        if runtime_project and not runtime_project.isdigit():
            return runtime_project

        # 2. Check explicitly initialized project_id if alphanumeric
        init_project = (self._project_id or "").strip()
        if init_project and not init_project.isdigit():
            return init_project

        # 3. Check other environment variables
        for env_var in ["BILLING_EXPORT_PROJECT_ID", "PROJECT_ID", "GCP_PROJECT"]:
            val = os.getenv(env_var, "").strip()
            if val and not val.isdigit():
                return val

        # 4. If only numeric ID is available, resolve to alphanumeric Project ID via Resource Manager
        candidate = runtime_project or init_project
        if candidate and candidate.isdigit():
            try:
                from google.cloud import resourcemanager_v3
                client = resourcemanager_v3.ProjectsClient(credentials=self.credentials)
                project = client.get_project(name=f"projects/{candidate}")
                if project.project_id:
                    self._project_id = project.project_id
                    return project.project_id
            except Exception as e:
                logger.warning("Failed to resolve project number '%s' to alphanumeric project ID: %s", candidate, e)

        return candidate

    @project_id.setter
    def project_id(self, value: str) -> None:
        self._project_id = value

    @property
    def scheduler_client(self) -> scheduler_v1.CloudSchedulerClient:
        """Lazily creates and caches the Cloud Scheduler client."""
        if self._scheduler_client is None:
            self._scheduler_client = scheduler_v1.CloudSchedulerClient(
                credentials=self.credentials
            )
        return self._scheduler_client

    @property
    def channel_client(self) -> monitoring_v3.NotificationChannelServiceClient:
        """Lazily creates and caches the Notification Channel client."""
        if self._channel_client is None:
            self._channel_client = monitoring_v3.NotificationChannelServiceClient(
                credentials=self.credentials
            )
        return self._channel_client

    @property
    def alert_policy_client(self) -> monitoring_v3.AlertPolicyServiceClient:
        """Lazily creates and caches the Alert Policy client."""
        if self._alert_policy_client is None:
            self._alert_policy_client = monitoring_v3.AlertPolicyServiceClient(
                credentials=self.credentials
            )
        return self._alert_policy_client

    @property
    def secret_client(self) -> secretmanager.SecretManagerServiceClient:
        """Lazily creates and caches the Secret Manager client."""
        if self._secret_client is None:
            self._secret_client = secretmanager.SecretManagerServiceClient(
                credentials=self.credentials
            )
        return self._secret_client

    async def close(self) -> None:
        """Closes client transport connections and releases resources."""
        self._scheduler_client = None
        self._channel_client = None
        self._alert_policy_client = None
        self._secret_client = None

    def get_agent_id_from_secrets(self) -> Optional[str]:
        """Fetches the Agent ID from environment variables, deployment metadata, or Secret Manager.

        Returns:
            Optional[str]: The secret value (Agent ID string) or None if not found/accessible.
        """
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
        if not self.project_id:
            logger.warning("Cannot fetch secret: project_id is not set.")
            return None

        candidate_secrets = [self.agent_id_secret_name]
        if "billing-concierge-agent-id" not in candidate_secrets:
            candidate_secrets.append("billing-concierge-agent-id")

        for sec_name in candidate_secrets:
            secret_path = f"projects/{self.project_id}/secrets/{sec_name}/versions/latest"
            try:
                response = self.secret_client.access_secret_version(
                    request={"name": secret_path}
                )
                return response.payload.data.decode("UTF-8").strip()
            except exceptions.NotFound:
                continue
            except exceptions.PermissionDenied:
                logger.error(
                    "Permission denied: Ensure the principal has 'Secret Manager Secret Accessor' on %s.",
                    sec_name,
                )
            except exceptions.InvalidArgument:
                logger.error(
                    "Invalid argument: Check if project_id '%s' is correct.",
                    self.project_id,
                )
            except Exception as e:
                logger.error("An unexpected error occurred while fetching secret '%s': %s", sec_name, e)

        logger.warning(
            "None of the candidate secrets (%s) found in project %s.",
            ", ".join(candidate_secrets),
            self.project_id,
        )
        return None

    def list_schedulers(self) -> List[Dict[str, str]]:
        """Lists all active billing audit schedules and their states in the current region.

        Returns:
            List[Dict[str, str]]: A list of scheduler jobs including name, schedule (cron), and state.
        """
        if not self.project_id:
            return [{"error": "GOOGLE_CLOUD_PROJECT is not configured."}]
        try:
            parent = f"projects/{self.project_id}/locations/{self.location}"
            jobs = self.scheduler_client.list_jobs(parent=parent)
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

    def list_channels(self) -> List[Dict[str, Any]]:
        """Lists all configured notification channels (emails) in the project.

        Returns:
            List[Dict[str, Any]]: A list of channels including display name, type, ID,
                                 email address, verification status, and enabled status.
        """
        if not self.project_id:
            return [{"error": "GOOGLE_CLOUD_PROJECT is not configured."}]
        try:
            project_name = f"projects/{self.project_id}"
            channels = self.channel_client.list_notification_channels(
                name=project_name
            )
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

    def list_policies(self) -> List[Dict[str, Any]]:
        """Lists all active monitoring alert policies with full details.

        Returns:
            List[Dict[str, Any]]: A list of alert policies including display name, enabled status, ID,
                                 linked notification channel IDs, and condition details.
        """
        if not self.project_id:
            return [{"error": "GOOGLE_CLOUD_PROJECT is not configured."}]
        try:
            project_name = f"projects/{self.project_id}"
            policies = self.alert_policy_client.list_alert_policies(
                name=project_name
            )
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

    def get_policy(self, policy_id: str) -> Dict[str, Any]:
        """Inspects and returns the full details of a specific alert policy.

        Args:
            policy_id: The full resource name (projects/PROJECT/alertPolicies/ID) or short ID of the alert policy.

        Returns:
            Dict[str, Any]: Detailed policy attributes including display name, enabled status, notification channels, and conditions.
        """
        if not self.project_id:
            return {"error": "GOOGLE_CLOUD_PROJECT is not configured."}
        try:
            name = (
                policy_id
                if policy_id.startswith("projects/")
                else f"projects/{self.project_id}/alertPolicies/{policy_id}"
            )
            p = self.alert_policy_client.get_alert_policy(name=name)
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
                    p.combiner.name
                    if hasattr(p.combiner, "name")
                    else str(p.combiner)
                ),
            }
        except Exception as e:
            logger.error("Failed to get alert policy '%s': %s", policy_id, e)
            return {"error": f"Failed to get alert policy '{policy_id.split('/')[-1]}'."}

    def setup_notification(self, email_address: str) -> str:
        """Creates a new email notification channel for billing alerts. Checks for duplicates first.

        Args:
            email_address: The valid email address to receive anomaly notifications.

        Returns:
            str: Status message indicating success, exists (if already exists), or error.
        """
        if not self.project_id:
            return "ERROR: GOOGLE_CLOUD_PROJECT is not configured."

        project_name = f"projects/{self.project_id}"

        # Duplicate check to prevent redundant channels
        existing = self.list_channels()
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
            response = self.channel_client.create_notification_channel(
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

    def setup_alert_policy(self, channel_ids: List[str]) -> str:
        """Creates or updates a log-based alert policy and links it to provided notification channel IDs.

        If the policy already exists, this method safely attaches any missing channel IDs,
        ensures the policy is enabled, and verifies the alert conditions.

        Args:
            channel_ids: A list of full resource names or short IDs for notification channels.

        Returns:
            str: Status message confirming creation, update, or verification of the policy.
        """
        if not self.project_id:
            return "ERROR: GOOGLE_CLOUD_PROJECT is not configured."

        project_name = f"projects/{self.project_id}"

        agent_name_env = os.getenv("AGENT_NAME", "billing-concierge")
        clean_prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", agent_name_env).lower().strip("-")
        if not clean_prefix or clean_prefix in ("gcp_billing_concierge", "gcp-billing-concierge"):
            policy_display_name = "billing-anomaly-detector"
            log_id_name = "billing-anomaly-detector"
        else:
            policy_display_name = f"{clean_prefix}-anomaly-detector"
            log_id_name = f"{clean_prefix}-anomaly-detector"

        robust_filter = (
            f'logName="projects/{self.project_id}/logs/{log_id_name}" OR '
            f'log_id("{log_id_name}")'
        )

        formatted_channel_ids = [
            cid if cid.startswith("projects/") else f"projects/{self.project_id}/notificationChannels/{cid}"
            for cid in channel_ids
        ]

        existing_policies = self.list_policies()
        matching_policy = next(
            (
                p
                for p in existing_policies
                if p.get("display_name") == policy_display_name
            ),
            None,
        )

        if matching_policy:
            policy_id = matching_policy.get("id")
            try:
                full_policy_name = (
                    policy_id
                    if policy_id.startswith("projects/")
                    else f"projects/{self.project_id}/alertPolicies/{policy_id}"
                )
                policy_obj = self.alert_policy_client.get_alert_policy(name=full_policy_name)
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
                    updated = self.alert_policy_client.update_alert_policy(
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

        # If not existing, create new policy
        alert_policy = {
            "display_name": policy_display_name,
            "combiner": monitoring_v3.AlertPolicy.ConditionCombinerType.OR,
            "conditions": [
                {
                    "display_name": f"Log match: {policy_display_name}",
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
            response = self.alert_policy_client.create_alert_policy(
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

    def schedule_audit(self, message: str, schedule: str, description: str) -> str:
        """Schedules or updates a recurring Cloud Scheduler billing audit job.

        Args:
            message: The prompt sent to the agent during scheduled runs (e.g.,
                "Compare the total cost of the entire previous calendar month against
                the average of the three months prior.").
            schedule: A standard 5-field cron expression (e.g., '0 9 * * 1' for Mondays at 9am).
            description: A short description ID of the scheduled job (e.g.
                monthly-audit, weekly-audit, daily-audit).

        Returns:
            str: Result message indicating if the scheduler was successfully created or updated.
        """
        if not self.project_id:
            return "ERROR: GOOGLE_CLOUD_PROJECT is not configured."

        agent_full_id = self.get_agent_id_from_secrets()
        if not agent_full_id:
            return (
                "ERROR: Could not retrieve Agent ID from Secret Manager. "
                "Ensure the agent is deployed and the agent ID secret is configured."
            )

        agent_name_env = os.getenv("AGENT_NAME", "billing-concierge")
        clean_prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", agent_name_env).lower().strip("-")
        if not clean_prefix or clean_prefix in ("gcp_billing_concierge", "gcp-billing-concierge"):
            clean_prefix = "billing-concierge"

        clean_desc = description.strip()
        if clean_desc.startswith(f"{clean_prefix}-"):
            short_desc = clean_desc[len(clean_prefix) + 1:]
        elif clean_desc.startswith("billing-concierge-"):
            short_desc = clean_desc[len("billing-concierge-"):]
        else:
            short_desc = clean_desc
        job_id = f"{clean_prefix}-{short_desc}"
        parent = f"projects/{self.project_id}/locations/{self.location}"
        job_name = f"{parent}/jobs/{job_id}"

        tz_str = self.timezone or os.getenv("TIMEZONE", "")
        if not tz_str:
            try:
                tz_str = tzlocal.get_localzone_name()
            except Exception:
                tz_str = "UTC"

        scheduler_sa = (
            os.getenv("SCHEDULER_SERVICE_ACCOUNT", "").strip()
            or os.getenv("AGENT_SERVICE_ACCOUNT", "").strip()
            or f"{self.service_account_id}@{self.project_id}.iam.gserviceaccount.com"
        )

        logger.info(
            "Configuring scheduler job '%s' with SA %s, timezone: %s",
            job_name,
            scheduler_sa,
            tz_str,
        )

        # Ensure resource_path is fully-qualified
        if not agent_full_id.startswith("projects/"):
            resource_path = f"projects/{self.project_id}/locations/{self.location}/reasoningEngines/{agent_full_id}"
        else:
            resource_path = agent_full_id

        # Align endpoint location with the resource path location
        endpoint_location = self.location
        parts = resource_path.split("/")
        if "locations" in parts:
            loc_idx = parts.index("locations") + 1
            if loc_idx < len(parts):
                path_location = parts[loc_idx]
                if path_location != self.location:
                    logger.warning(
                        "Agent resource location '%s' does not match regional infrastructure location '%s'. Using '%s'.",
                        path_location,
                        self.location,
                        path_location,
                    )
                endpoint_location = path_location

        target_uri = f"https://{endpoint_location}-aiplatform.googleapis.com/v1/{resource_path}:streamQuery"

        job = {
            "name": job_name,
            "schedule": schedule,
            "time_zone": tz_str,
            "http_target": {
                "uri": target_uri,
                "http_method": scheduler_v1.HttpMethod.POST,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(
                    {
                        "class_method": "async_stream_query",
                        "input": {
                            "user_id": f"{clean_prefix.replace('-', '_')}_audit",
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
            self.scheduler_client.create_job(parent=parent, job=job)
            return f"SUCCESS: Created scheduler job '{job_id}' with schedule '{schedule}'."
        except exceptions.AlreadyExists:
            update_mask = {"paths": ["schedule", "http_target", "time_zone"]}
            self.scheduler_client.update_job(job=job, update_mask=update_mask)
            return f"SUCCESS: Updated existing scheduler job '{job_id}' to schedule '{schedule}'."
        except Exception as e:
            logger.error("ERROR: Failed to schedule audit: %s", str(e))
            return "ERROR: Failed to schedule audit. Please check Cloud Scheduler permissions."

    def delete_resource(self, resource_name: str, resource_type: str) -> str:
        """Deletes a FinOps infrastructure resource (scheduler, channel, or policy).

        Args:
            resource_name: The resource identifier or name to be deleted.
            resource_type: The category of resource. Must be 'scheduler', 'channel', or 'policy'.

        Returns:
            str: Success message or the specific error encountered during deletion.
        """
        short_name = resource_name.split("/")[-1]
        try:
            if resource_type == "scheduler":
                agent_name_env = os.getenv("AGENT_NAME", "billing-concierge")
                clean_prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", agent_name_env).lower().strip("-")
                if not clean_prefix or clean_prefix in ("gcp_billing_concierge", "gcp-billing-concierge"):
                    clean_prefix = "billing-concierge"

                if resource_name.startswith("projects/"):
                    target_name = resource_name
                elif resource_name.startswith(f"{clean_prefix}-") or resource_name.startswith("billing-concierge-"):
                    target_name = f"projects/{self.project_id}/locations/{self.location}/jobs/{resource_name}"
                else:
                    target_name = f"projects/{self.project_id}/locations/{self.location}/jobs/{clean_prefix}-{resource_name}"

                try:
                    self.scheduler_client.delete_job(name=target_name)
                except exceptions.NotFound:
                    fallback_name = f"projects/{self.project_id}/locations/{self.location}/jobs/{resource_name}"
                    self.scheduler_client.delete_job(name=fallback_name)

            elif resource_type == "channel":
                target_name = (
                    resource_name
                    if resource_name.startswith("projects/")
                    else f"projects/{self.project_id}/notificationChannels/{resource_name}"
                )
                self.channel_client.delete_notification_channel(
                    name=target_name, force=True
                )
            elif resource_type == "policy":
                if resource_name.startswith("projects/"):
                    target_name = resource_name
                elif resource_name.isdigit():
                    target_name = f"projects/{self.project_id}/alertPolicies/{resource_name}"
                else:
                    policies = self.list_policies()
                    matched = next(
                        (p for p in policies if p.get("display_name") == resource_name or p.get("id") == resource_name),
                        None,
                    )
                    if matched and matched.get("id"):
                        target_name = f"projects/{self.project_id}/alertPolicies/{matched['id']}"
                    else:
                        target_name = f"projects/{self.project_id}/alertPolicies/{resource_name}"
                self.alert_policy_client.delete_alert_policy(name=target_name)
            else:
                return (
                    f"ERROR: Unknown resource type '{resource_type}'. "
                    "Must be 'scheduler', 'channel', or 'policy'."
                )

            return f"SUCCESS: Deleted {resource_type} '{short_name}'."
        except exceptions.NotFound:
            return f"SKIP: Resource '{short_name}' not found."
        except Exception as e:
            logger.error("Failed to delete %s (%s): %s", resource_type, resource_name, e)
            return f"ERROR: Failed to delete {resource_type} '{short_name}'."

    async def get_tools(
        self, readonly_context: Optional[ReadonlyContext] = None
    ) -> list[BaseTool]:
        """Returns all exposed infrastructure tools in the toolset.

        Applies confirmation guardrails to destructive operations and respects
        any configured tool_filter.
        """
        tools: list[BaseTool] = [
            FunctionTool(self.list_schedulers),
            FunctionTool(self.list_channels),
            FunctionTool(self.list_policies),
            FunctionTool(self.get_policy),
            FunctionTool(self.setup_notification),
            FunctionTool(self.setup_alert_policy),
            FunctionTool(self.schedule_audit),
            FunctionTool(
                self.delete_resource,
                require_confirmation=self.require_confirmation_for_delete,
            ),
        ]
        return [t for t in tools if self._is_tool_selected(t, readonly_context)]
