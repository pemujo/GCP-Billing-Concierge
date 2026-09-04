import logging
import os
import pathlib
from typing import Any, Optional

import google.auth
import google.cloud.logging
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.skills import load_skill_from_dir
from google.adk.tools.bigquery import (
    BigQueryCredentialsConfig,
)
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode
from .tools.finops_bigquery_toolset import FinOpsBigQueryToolset
from google.adk.tools.skill_toolset import SkillToolset
from google.auth.transport.requests import Request

# Internal Imports
from .prompt import get_instructions
from .sub_agents.finops_infra_agent.agent import finops_infra_agent
from .tools.tools import log_billing_anomaly

# Initialization
load_dotenv()
if os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GEMINI_API_KEY"):
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
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
    logger.warning(
        "Billing export table environment variables (BILLING_EXPORT_PROJECT_ID, "
        "BILLING_EXPORT_DATASET, BILLING_EXPORT_TABLE) are not fully set."
    )

AGENT_NAME = "GCP_billing_concierge"

# Environment & Auth Initialization with Safe Fallback
credentials: Optional[Any] = None
bq_credentials_config: Optional[BigQueryCredentialsConfig] = None
logging_client: Optional[google.cloud.logging.Client] = None

try:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    auth_request = Request()
    credentials.refresh(auth_request)
    bq_credentials_config = BigQueryCredentialsConfig(credentials=credentials)

    if AGENT_PROJECT_ID:
        logging_client = google.cloud.logging.Client(
            project=AGENT_PROJECT_ID, credentials=credentials
        )
except Exception as e:
    logger.warning(
        "Running in local/offline mode or credentials could not be initialized: %s",
        e,
    )

# BigQuery FinOps Toolset & Guardrails Setup
MAX_BYTES_BILLED = int(os.getenv("BQ_MAX_BYTES_BILLED", "1073741824"))  # 1 GiB budget limit
MAX_RESULT_ROWS = int(os.getenv("BQ_MAX_QUERY_RESULT_ROWS", "50"))

bq_guardrail_config = BigQueryToolConfig(
    write_mode=WriteMode.BLOCKED,
    maximum_bytes_billed=MAX_BYTES_BILLED,
    max_query_result_rows=MAX_RESULT_ROWS,
    compute_project_id=AGENT_PROJECT_ID or None,
    location=GOOGLE_CLOUD_LOCATION,
    application_name="gcp-billing-concierge",
    job_labels={"env": "production", "workload": "finops-analysis"},
)
bigquery_toolset = FinOpsBigQueryToolset(
    credentials_config=bq_credentials_config,
    bigquery_tool_config=bq_guardrail_config,
    max_bytes_billed=MAX_BYTES_BILLED,
    tool_filter=[
        "get_table_info",
        "execute_sql",
        "get_job_info",
    ],
)

# --- Tool Wrappers


def log_anomaly(anomaly_type: str, severity: str, details: str) -> str:
    """
    Logs a detected billing anomaly to Cloud Logging for audit and alerting.

    This function acts as a wrapper for `log_billing_anomaly`, allowing the agent
    to record specific findings that can later trigger alert policies.

    Args:
        anomaly_type (str): The category of the anomaly (e.g., 'Sudden Spike', 'New Service', 'Cost Anomaly').
        severity (str): The severity level ('CRITICAL', 'HIGH', 'ERROR', 'MEDIUM', 'WARNING', 'LOW', 'INFO', 'URGENT').
        details (str): A descriptive explanation of the billing anomaly detected.

    Returns:
        str: Confirmation message or error status of the logging operation.
    """
    global logging_client
    if logging_client is None and AGENT_PROJECT_ID and credentials:
        try:
            logging_client = google.cloud.logging.Client(
                project=AGENT_PROJECT_ID, credentials=credentials
            )
        except Exception as e:
            return f"ERROR: Logging client unavailable: {e}"

    if logging_client is None:
        return "ERROR: Cloud Logging client is not initialized. Please verify credentials and GOOGLE_CLOUD_PROJECT."

    return log_billing_anomaly(
        logging_client=logging_client,
        project_id=AGENT_PROJECT_ID,
        full_table_path=FULL_TABLE_PATH,
        anomaly_type=anomaly_type,
        severity=severity,
        details=details,
    )


# Skills Setup
skills_dir = pathlib.Path(__file__).parent / "skills"
skills = []
try:
    if skills_dir.exists():
        for skill_path in sorted(skills_dir.iterdir()):
            if skill_path.is_dir() and (skill_path / "SKILL.md").exists():
                skills.append(load_skill_from_dir(skill_path))
        logger.info("Successfully loaded %d skills from %s", len(skills), skills_dir)
except Exception as e:
    logger.warning("Could not load skills from %s: %s", skills_dir, e)

skill_toolset = SkillToolset(skills=skills)

# Final Agent Definition
billing_concierge_agent = Agent(
    model=GEMINI_MODEL,
    name=AGENT_NAME,
    description="FinOps agent for GCP Billing analysis, anomaly logging, and monitoring infrastructure.",
    instruction=get_instructions(
        FULL_TABLE_PATH, AGENT_PROJECT_ID, GOOGLE_CLOUD_LOCATION
    ),
    sub_agents=[finops_infra_agent],
    tools=[
        bigquery_toolset,
        log_anomaly,
        skill_toolset,
    ],
)

root_agent = billing_concierge_agent
