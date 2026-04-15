import logging
import os
from typing import Any

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
from .sub_agents.finops_infra_agent.agent import finops_infra_agent
from .tools.tools import (
    log_billing_anomaly,
)

# Initialization
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment & Auth
try:
    """
    Global initialization block for GCP credentials and environment variables.
    
    This block:
    1. Authenticates using Application Default Credentials (ADC).
    2. Configures BigQuery credentials for the toolset.
    3. Validates required Project ID and Location environment variables.
    4. Initializes the Cloud Logging client.
    """
    credentials, _ = google.auth.default(
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

except Exception:
    logger.exception("Failed to initialize GCP environment or credentials.")
    raise

# Config Constants
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
BILLING_PROJECT = os.getenv("BILLING_EXPORT_PROJECT_ID")
BILLING_DATASET = os.getenv("BILLING_EXPORT_DATASET")
BILLING_TABLE = os.getenv("BILLING_EXPORT_TABLE")
FULL_TABLE_PATH = f"{BILLING_PROJECT}.{BILLING_DATASET}.{BILLING_TABLE}"
AGENT_NAME = "GCP_billing_concierge"

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

# --- Tool Wrappers


def log_anomaly(anomaly_type: str, severity: str, details: str) -> Any:
    """
    Logs a detected billing anomaly to Cloud Logging for audit and alerting.

    This function acts as a wrapper for `log_billing_anomaly`, allowing the agent
    to record specific findings that can later trigger alert policies.

    Args:
        anomaly_type (str): The category of the anomaly (e.g., 'Sudden Spike', 'New Service').
        severity (str): The severity level (e.g., 'INFO', 'WARNING', 'CRITICAL').
        details (str): A descriptive explanation of the billing anomaly detected.

    Returns:
        Any: The result of the logging operation (typically a log entry reference or status).
    """
    return log_billing_anomaly(
        logging_client,
        AGENT_PROJECT_ID,
        FULL_TABLE_PATH,
        anomaly_type,
        severity,
        details,
    )


# Final Agent Definition
"""
billing_concierge_agent (Agent): The main entry point for the Billing Concierge solution. 

It combines:
- BigQuery Toolset: To query and analyze billing export data.
- log_anomaly Tool: To report findings to GCP Monitoring.
- alerting_agent: A sub-agent dedicated to managing infrastructure lifecycle 
  (schedulers and notification channels).
"""
billing_concierge_agent = Agent(
    model=GEMINI_MODEL,
    name=AGENT_NAME,
    description="FinOps agent for GCP Billing analysis and anomaly logging.",
    instruction=get_instructions(
        FULL_TABLE_PATH, AGENT_PROJECT_ID, GOOGLE_CLOUD_LOCATION
    ),
    sub_agents=[finops_infra_agent],
    tools=[
        bigquery_toolset,
        log_anomaly,
    ],
)

root_agent = billing_concierge_agent