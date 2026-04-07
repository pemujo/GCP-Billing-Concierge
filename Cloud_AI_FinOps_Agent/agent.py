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
from .utils.tools import log_billing_anomaly

# Initialization
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment & Auth
try:
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

    logging_client = google.cloud.logging.Client(
        project=AGENT_PROJECT_ID, credentials=credentials
    )

except Exception:
    # This captures the full traceback, not just the error message
    logger.exception("Failed to initialize GCP environment or credentials.")
    raise

# Config Constants
GEMINI_MODEL = os.getenv("BIGQUERY_AGENT_MODEL", "gemini-2.5-flash")
FULL_TABLE_PATH = f"""{os.getenv("BILLING_EXPORT_PROJECT_ID")}.
                {os.getenv("BILLING_EXPORT_DATASET")}.{os.getenv("BILLING_EXPORT_TABLE")}"""

# Toolset Setup
bq_read_only_config = BigQueryToolConfig(write_mode=WriteMode.BLOCKED)
bigquery_toolset = BigQueryToolset(
    credentials_config=bq_credentials_config,
    bigquery_tool_config=bq_read_only_config,
    tool_filter=[
        "get_table_info",
        "execute_sql",
        "ask_data_insights",
        "get_job_info",
    ],
)


# Functional Wrapper for Tool
def log_anomaly_wrapper(anomaly_type: str, severity: str, details: str):
    return log_billing_anomaly(
        logging_client,
        AGENT_PROJECT_ID,
        FULL_TABLE_PATH,
        anomaly_type,
        severity,
        details,
    )


# Final Agent Definition
billing_agent = Agent(
    model=GEMINI_MODEL,
    name="gcp_billing_concierge",
    description="FinOps agent for GCP Billing analysis and anomaly logging.",
    instruction=get_instructions(FULL_TABLE_PATH, AGENT_PROJECT_ID),
    tools=[bigquery_toolset, log_anomaly_wrapper],
)

root_agent = billing_agent
