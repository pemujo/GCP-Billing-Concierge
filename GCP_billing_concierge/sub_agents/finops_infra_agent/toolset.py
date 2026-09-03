"""FinOps Infrastructure Toolset for GCP Billing Concierge.

Encapsulates Cloud Scheduler, Cloud Monitoring (Alert Policies & Notification Channels),
and Secret Manager tools inside a standardized ADK BaseToolset.
"""

from __future__ import annotations

import json
import logging
import os
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
        location: str = "us-central1",
        credentials: Optional[Any] = None,
        timezone: Optional[str] = None,
        service_account_id: str = "gcp-billing-concierge-sa",
        agent_id_secret_name: str = "billing-concierge-agent-id",
        require_confirmation_for_delete: bool = True,
        *,
        tool_filter: Optional[Union[ToolPredicate, List[str]]] = None,
        tool_name_prefix: Optional[str] = None,
    ):
        """Initializes the FinOps Infrastructure Toolset.

        Args:
            project_id: The GCP Project ID where resources and secrets reside.
            location: The GCP region for regional resources like Cloud Scheduler.
            credentials: Optional Google Auth credentials.
            timezone: Optional IANA timezone string for cron schedules.
            service_account_id: The SA ID prefix used by Cloud Scheduler to trigger the agent.
            agent_id_secret_name: Secret name storing the Reasoning Engine / Agent Runtime ID.
            require_confirmation_for_delete: Whether delete_resource requires user confirmation.
            tool_filter: Optional filter to expose a subset of tools.
            tool_name_prefix: Optional prefix for all tool names.
        """
        super().__init__(tool_filter=tool_filter, tool_name_prefix=tool_name_prefix)
        self.project_id = project_id
        self.location = location
        self.credentials = credentials
        self.timezone = timezone
        self.service_account_id = service_account_id
        self.agent_id_secret_name = agent_id_secret_name
        self.require_confirmation_for_delete = require_confirmation_for_delete

        # Lazily-cached client instances
        self._scheduler_client: Optional[scheduler_v1.CloudSchedulerClient] = None
        self._channel_client: Optional[monitoring_v3.NotificationChannelServiceClient] = None
        self._alert_policy_client: Optional[monitoring_v3.AlertPolicyServiceClient] = None
        self._secret_client: Optional[secretmanager.SecretManagerServiceClient] = None

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
        """Fetches the latest Agent ID from Secret Manager with error handling.

        Returns:
            Optional[str]: The secret value (Agent ID string) or None if not found/accessible.
        """
        if not self.project_id:
            logger.warning("Cannot fetch secret: project_id is not set.")
            return None

        secret_path = f"projects/{self.project_id}/secrets/{self.agent_id_secret_name}/versions/latest"
        try:
            response = self.secret_client.access_secret_version(
                request={"name": secret_path}
            )
            return response.payload.data.decode("UTF-8").strip()
        except exceptions.NotFound:
            logger.warning(
                "Secret '%s' or version 'latest' not found in project %s.",
                self.agent_id_secret_name,
                self.project_id,
            )
        except exceptions.PermissionDenied:
            logger.error(
                "Permission denied: Ensure the SA has 'Secret Manager Secret Accessor' on %s.",
                self.agent_id_secret_name,
            )
        except exceptions.InvalidArgument:
            logger.error(
                "Invalid argument: Check if project_id '%s' is correct.",
                self.project_id,
            )
        except Exception as e:
            logger.error("An unexpected error occurred while fetching secret: %s", e)

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
                {"name": j.name, "schedule": j.schedule, "state": j.state.name}
                for j in jobs
            ]
        except Exception as e:
            logger.error("Failed to list scheduler jobs: %s", e)
            return [{"error": f"Failed to list scheduler jobs: {str(e)}"}]

    def list_channels(self) -> List[Dict[str, Any]]:
        """Lists all configured notification channels (emails) in the project.

        Returns:
            List[Dict[str, Any]]: A list of channels including display name, type, ID, and email address.
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
                    "id": c.name,
                    "email": c.labels.get("email_address"),
                }
                for c in channels
            ]
        except Exception as e:
            logger.error("Failed to list notification channels: %s", e)
            return [{"error": f"Failed to list notification channels: {str(e)}"}]

    def list_policies(self) -> List[Dict[str, Any]]:
        """Lists all active monitoring alert policies.

        Returns:
            List[Dict[str, Any]]: A list of alert policies including display name, enabled status, and ID.
        """
        if not self.project_id:
            return [{"error": "GOOGLE_CLOUD_PROJECT is not configured."}]
        try:
            project_name = f"projects/{self.project_id}"
            policies = self.alert_policy_client.list_alert_policies(
                name=project_name
            )
            return [
                {"display_name": p.display_name, "enabled": p.enabled, "id": p.name}
                for p in policies
            ]
        except Exception as e:
            logger.error("Failed to list alert policies: %s", e)
            return [{"error": f"Failed to list alert policies: {str(e)}"}]

    def setup_notification(self, email_address: str) -> str:
        """Creates a new email notification channel for billing alerts. Checks for duplicates first.

        Args:
            email_address: The valid email address to receive anomaly notifications.

        Returns:
            str: Status message indicating success, skip (if already exists), or error.
        """
        if not self.project_id:
            return "ERROR: GOOGLE_CLOUD_PROJECT is not configured."

        project_name = f"projects/{self.project_id}"

        # Duplicate check to prevent redundant channels
        existing = self.list_channels()
        for channel in existing:
            if channel.get("email") == email_address:
                return (
                    f"SKIP: Notification channel for {email_address} already exists ({channel.get('id')})."
                )

        try:
            channel_data = {
                "display_name": f"FinOps Alert: {email_address}",
                "type": "email",
                "labels": {"email_address": email_address},
            }
            response = self.channel_client.create_notification_channel(
                name=project_name, notification_channel=channel_data
            )
            return f"SUCCESS: Created channel {response.name}"
        except Exception as e:
            logger.error("Failed to create notification channel: %s", e)
            return f"ERROR: Failed to create channel: {str(e)}"

    def setup_alert_policy(self, channel_ids: List[str]) -> str:
        """Creates a log-based alert policy and links it to provided notification channel IDs.

        Args:
            channel_ids: A list of full resource names for notification channels.

        Returns:
            str: Status message confirming the creation or skip-status of the policy.
        """
        if not self.project_id:
            return "ERROR: GOOGLE_CLOUD_PROJECT is not configured."

        project_name = f"projects/{self.project_id}"

        # Duplicate Check
        existing = self.list_policies()
        if any(p.get("display_name") == "billing-anomaly-detector" for p in existing):
            return "SKIP: Alert policy 'billing-anomaly-detector' already exists."

        alert_policy = {
            "display_name": "billing-anomaly-detector",
            "combiner": monitoring_v3.AlertPolicy.ConditionCombinerType.OR,
            "conditions": [
                {
                    "display_name": "Log match: billing-anomaly-detector",
                    "condition_matched_log": {
                        "filter": f'logName="projects/{self.project_id}/logs/billing-anomaly-detector"',
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
            response = self.alert_policy_client.create_alert_policy(
                name=project_name, alert_policy=alert_policy
            )
            return f"SUCCESS: Created Alert Policy {response.name}"
        except Exception as e:
            logger.error("Failed to create alert policy: %s", e)
            return f"ERROR: Failed to create alert policy: {str(e)}"

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
                f"ERROR: Could not retrieve Reasoning Engine Agent ID from Secret Manager in project '{self.project_id}'. "
                f"Ensure the agent is deployed and secret '{self.agent_id_secret_name}' is populated."
            )

        parent = f"projects/{self.project_id}/locations/{self.location}"
        job_name = f"{parent}/jobs/billing-concierge-{description}"

        tz_str = self.timezone or os.getenv("TIMEZONE", "")
        if not tz_str:
            try:
                tz_str = tzlocal.get_localzone_name()
            except Exception:
                tz_str = "UTC"

        scheduler_sa = f"{self.service_account_id}@{self.project_id}.iam.gserviceaccount.com"

        logger.info(
            "Configuring scheduler job '%s' with SA %s, timezone: %s",
            job_name,
            scheduler_sa,
            tz_str,
        )

        job = {
            "name": job_name,
            "schedule": schedule,
            "time_zone": tz_str,
            "http_target": {
                "uri": f"https://{self.location}-aiplatform.googleapis.com/v1/{agent_full_id}:streamQuery",
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
            self.scheduler_client.create_job(parent=parent, job=job)
            return f"SUCCESS: Created scheduler job '{description}' with schedule '{schedule}'"
        except exceptions.AlreadyExists:
            update_mask = {"paths": ["schedule", "http_target", "time_zone"]}
            self.scheduler_client.update_job(job=job, update_mask=update_mask)
            return f"SUCCESS: Updated existing scheduler job '{description}' to schedule '{schedule}'"
        except Exception as e:
            logger.error("ERROR: Failed to schedule audit: %s", str(e))
            return f"ERROR: Failed to schedule audit: {str(e)}"

    def delete_resource(self, resource_name: str, resource_type: str) -> str:
        """Deletes a FinOps infrastructure resource (scheduler, channel, or policy).

        Args:
            resource_name: The full resource identifier/name to be deleted.
            resource_type: The category of resource. Must be 'scheduler', 'channel', or 'policy'.

        Returns:
            str: Success message or the specific error encountered during deletion.
        """
        try:
            if resource_type == "scheduler":
                self.scheduler_client.delete_job(name=resource_name)
            elif resource_type == "channel":
                self.channel_client.delete_notification_channel(
                    name=resource_name, force=True
                )
            elif resource_type == "policy":
                self.alert_policy_client.delete_alert_policy(name=resource_name)
            else:
                return (
                    f"ERROR: Unknown resource type '{resource_type}'. "
                    "Must be 'scheduler', 'channel', or 'policy'."
                )

            return f"SUCCESS: Deleted {resource_type}: {resource_name}"
        except exceptions.NotFound:
            return f"SKIP: Resource {resource_name} not found."
        except Exception as e:
            logger.error("Failed to delete %s (%s): %s", resource_type, resource_name, e)
            return f"ERROR: Failed to delete {resource_name}: {str(e)}"

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
            FunctionTool(self.setup_notification),
            FunctionTool(self.setup_alert_policy),
            FunctionTool(self.schedule_audit),
            FunctionTool(
                self.delete_resource,
                require_confirmation=self.require_confirmation_for_delete,
            ),
        ]
        return [t for t in tools if self._is_tool_selected(t, readonly_context)]
