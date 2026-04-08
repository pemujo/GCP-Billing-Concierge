import logging
import os

import google.auth
import google.cloud.logging
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.tools.bigquery import (
    BigQueryCredentialsConfig,
    BigQueryToolset,
)
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode
from google.auth.transport.requests import Request

# Internal Imports
from .prompt import get_instructions
from .tools.tools import (
    log_billing_anomaly,
    get_agent_id_from_secrets,
    list_active_schedulers,
    list_notification_channels,
    list_alert_policies,
    create_billing_notification_channel,
    create_billing_alert_policy,
    schedule_audit,
    delete_finops_resource
)

# Initialization
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment & Auth
try:
    credentials, project_id = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )

    bq_credentials_config = BigQueryCredentialsConfig(credentials=credentials)
    auth_request = Request()
    credentials.refresh(auth_request)

    AGENT_PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not AGENT_PROJECT_ID:
        raise ValueError(
            "GOOGLE_CLOUD_PROJECT is not set in environment or .env file."
        )
    
    GOOGLE_CLOUD_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION")
    if not GOOGLE_CLOUD_LOCATION:
        raise ValueError(
            "GOOGLE_CLOUD_LOCATION is not set in environment or .env file."
        )

    logging_client = google.cloud.logging.Client(
        project=AGENT_PROJECT_ID, credentials=credentials
    )

    # Fetch Agent ID for Scheduler tools
    try:
        AGENT_ENGINE_ID = get_agent_id_from_secrets(AGENT_PROJECT_ID)
    except Exception as e:
        # During local dev, this will likely fail. We log it and move on.
        logger.warning("""Running in local/dev mode: 
                       AGENT_ENGINE_ID not found in Secret Manager.""")
        AGENT_ENGINE_ID = "PENDING_DEPLOYMENT"

except Exception:
    # This captures the full traceback, not just the error message
    logger.exception("Failed to initialize GCP environment or credentials.")
    raise

# Config Constants
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
BILLING_PROJECT = os.getenv("BILLING_EXPORT_PROJECT_ID")
BILLING_DATASET = os.getenv("BILLING_EXPORT_DATASET")
BILLING_TABLE = os.getenv("BILLING_EXPORT_TABLE")
FULL_TABLE_PATH = f"{BILLING_PROJECT}.{BILLING_DATASET}.{BILLING_TABLE}"

# Toolset Setup
bq_read_only_config = BigQueryToolConfig(write_mode=WriteMode.BLOCKED)
bigquery_toolset = BigQueryToolset(
    credentials_config=bq_credentials_config,
    bigquery_tool_config=bq_read_only_config,
    tool_filter=[
        "get_table_info",
        "execute_sql",
        "get_job_info",
    ],
)

# --- Functional Wrappers (Hiding Metadata from LLM) ---

def log_anomaly_wrapper(anomaly_type: str, severity: str, details: str):
    """Logs a detected billing anomaly to Cloud Logging."""
    return log_billing_anomaly(
        logging_client,
        AGENT_PROJECT_ID,
        FULL_TABLE_PATH,
        anomaly_type,
        severity,
        details,
    )

def list_schedulers_wrapper():
    """Lists all active billing audit schedules and their states."""
    return list_active_schedulers(AGENT_PROJECT_ID, GOOGLE_CLOUD_LOCATION)

def list_channels_wrapper():
    """Lists all configured notification channels (emails) in the project."""
    return list_notification_channels(AGENT_PROJECT_ID)

def list_policies_wrapper():
    """Lists all active monitoring alert policies."""
    return list_alert_policies(AGENT_PROJECT_ID)

def setup_notification_wrapper(email_address: str):
    """Creates a new email notification channel for billing alerts."""
    return create_billing_notification_channel(AGENT_PROJECT_ID, email_address)

def setup_alert_policy_wrapper(channel_ids: list[str]):
    """Links billing log alerts to specific notification channel IDs."""
    return create_billing_alert_policy(AGENT_PROJECT_ID, channel_ids)

def schedule_audit_wrapper(schedule: str):
    """
    Schedules or updates a recurring billing audit job.
    Args:
        schedule: A cron expression (e.g., '0 9 * * 1' for Mondays at 9am).
    """
    return schedule_audit(
        AGENT_PROJECT_ID, 
        GOOGLE_CLOUD_LOCATION, 
        AGENT_ENGINE_ID, 
        schedule
    )

def delete_resource_wrapper(resource_name: str, resource_type: str):
    """
    Deletes a FinOps infrastructure resource (scheduler, channel, or policy).
    Args:
        resource_name: The full resource name/ID.
        resource_type: Must be 'scheduler', 'channel', or 'policy'.
    """
    return delete_finops_resource(resource_name, resource_type)



# Final Agent Definition
billing_agent = Agent(
    model=GEMINI_MODEL,
    name="gcp_billing_concierge",
    description="FinOps agent for GCP Billing analysis and anomaly logging.",
    instruction=get_instructions(FULL_TABLE_PATH, AGENT_PROJECT_ID, GOOGLE_CLOUD_LOCATION),
    tools=[
        bigquery_toolset, 
        log_anomaly_wrapper,
        list_schedulers_wrapper,
        list_channels_wrapper,
        list_policies_wrapper,
        setup_notification_wrapper,
        setup_alert_policy_wrapper,
        schedule_audit_wrapper,
        delete_resource_wrapper
    ],
)

root_agent = billing_agent
