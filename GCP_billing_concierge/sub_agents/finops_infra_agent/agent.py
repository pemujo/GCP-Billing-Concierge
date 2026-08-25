import logging
import os
from typing import Any, Dict, List, Optional

import google.auth
from dotenv import load_dotenv
from google.adk.agents import Agent

# Internal Imports
from .prompt import get_instructions
from .tools.tools import (
    create_billing_alert_policy,
    create_billing_notification_channel,
    create_scheduler,
    delete_finops_resource,
    list_active_schedulers,
    list_alert_policies,
    list_notification_channels,
)

# Initialization
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config Constants & Environment Variables
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
AGENT_PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "")
GOOGLE_CLOUD_LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
BILLING_PROJECT = os.getenv("BILLING_EXPORT_PROJECT_ID", AGENT_PROJECT_ID)
BILLING_DATASET = os.getenv("BILLING_EXPORT_DATASET", "")
BILLING_TABLE = os.getenv("BILLING_EXPORT_TABLE", "")

if BILLING_PROJECT and BILLING_DATASET and BILLING_TABLE:
    FULL_TABLE_PATH = f"{BILLING_PROJECT}.{BILLING_DATASET}.{BILLING_TABLE}"
else:
    FULL_TABLE_PATH = "billing_export_table_not_configured"

AGENT_NAME = "Finops_infra_agent"

# Auth Check / Initialization
credentials: Optional[Any] = None
try:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
except Exception as e:
    logger.warning("Running in local/offline mode or credentials not found: %s", e)


# --- Tool Wrappers


def list_schedulers() -> List[Dict[str, str]]:
    """
    Lists all active billing audit schedules and their states in the current region.

    Returns:
        List[Dict[str, str]]: A list of scheduler jobs including name, schedule (cron), and state.
    """
    if not AGENT_PROJECT_ID:
        return [{"error": "GOOGLE_CLOUD_PROJECT is not set."}]
    return list_active_schedulers(AGENT_PROJECT_ID, GOOGLE_CLOUD_LOCATION)


def list_channels() -> List[Dict[str, Any]]:
    """
    Lists all configured notification channels (emails) in the project.

    Returns:
        List[Dict[str, Any]]: A list of channels including display names and email addresses.
    """
    if not AGENT_PROJECT_ID:
        return [{"error": "GOOGLE_CLOUD_PROJECT is not set."}]
    return list_notification_channels(AGENT_PROJECT_ID)


def list_policies() -> List[Dict[str, Any]]:
    """
    Lists all active monitoring alert policies.

    Returns:
        List[Dict[str, Any]]: A list of alert policies including status and resource IDs.
    """
    if not AGENT_PROJECT_ID:
        return [{"error": "GOOGLE_CLOUD_PROJECT is not set."}]
    return list_alert_policies(AGENT_PROJECT_ID)


def setup_notification(email_address: str) -> str:
    """
    Creates a new email notification channel for billing alerts.

    Args:
        email_address (str): The valid email address to receive anomaly notifications.

    Returns:
        str: A message indicating the success or failure of the channel creation.
    """
    if not AGENT_PROJECT_ID:
        return "ERROR: GOOGLE_CLOUD_PROJECT is not set."
    return create_billing_notification_channel(AGENT_PROJECT_ID, email_address)


def setup_alert_policy(channel_ids: List[str]) -> str:
    """
    Links billing log alerts to specific notification channel IDs.

    Args:
        channel_ids (List[str]): A list of full resource names for notification channels.

    Returns:
        str: Status message confirming the creation or skip-status of the policy.
    """
    if not AGENT_PROJECT_ID:
        return "ERROR: GOOGLE_CLOUD_PROJECT is not set."
    return create_billing_alert_policy(AGENT_PROJECT_ID, channel_ids)


def schedule_audit(message: str, schedule: str, description: str) -> str:
    """
    Schedules or updates a recurring billing audit job.

    Args:
        message (str): The prompt sent to the agent (e.g., Compare the total cost of 
                       the entire previous calendar month against the average of the three months prior.)
        schedule (str): A cron expression (e.g., '0 9 * * 1' for Mondays at 9am).
        description (str): A short description ID of the scheduled job 
                           (e.g. monthly-audit, weekly-audit, daily-audit)

    Returns:
        str: Result message indicating if the scheduler was successfully created or updated.
    """
    if not AGENT_PROJECT_ID:
        return "ERROR: GOOGLE_CLOUD_PROJECT is not set."
    return create_scheduler(
        AGENT_PROJECT_ID,
        GOOGLE_CLOUD_LOCATION,
        message,
        schedule,
        description,
    )


def delete_resource(resource_name: str, resource_type: str) -> str:
    """
    Deletes a FinOps infrastructure resource (scheduler, channel, or policy).

    Args:
        resource_name (str): The full resource name/ID to be deleted.
        resource_type (str): The category of resource. Must be 'scheduler', 'channel', or 'policy'.

    Returns:
        str: Success message or the specific error encountered during deletion.
    """
    return delete_finops_resource(resource_name, resource_type)


# Agent Definition
finops_infra_agent = Agent(
    model=GEMINI_MODEL,
    name=AGENT_NAME,
    description=(
        "Agent specialized to manage audit lifecycle including Cloud Schedulers, "
        "Alerts, and Notifications of the billing anomalies reported by the Billing concierge agent."
    ),
    instruction=get_instructions(
        FULL_TABLE_PATH, AGENT_PROJECT_ID, GOOGLE_CLOUD_LOCATION
    ),
    tools=[
        list_schedulers,
        list_channels,
        list_policies,
        setup_notification,
        setup_alert_policy,
        schedule_audit,
        delete_resource,
    ],
)
