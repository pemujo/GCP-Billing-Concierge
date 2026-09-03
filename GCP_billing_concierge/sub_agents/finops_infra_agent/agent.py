import logging
import os
from typing import Any, Optional

import google.auth
from dotenv import load_dotenv
from google.adk.agents import Agent

# Internal Imports
from .prompt import get_instructions
from .toolset import FinOpsInfraToolset

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

# FinOps Infrastructure Toolset
finops_infra_toolset = FinOpsInfraToolset(
    project_id=AGENT_PROJECT_ID,
    location=GOOGLE_CLOUD_LOCATION,
    credentials=credentials,
    require_confirmation_for_delete=True,
)

# Backward-compatibility handles
list_schedulers = finops_infra_toolset.list_schedulers
list_channels = finops_infra_toolset.list_channels
list_policies = finops_infra_toolset.list_policies
setup_notification = finops_infra_toolset.setup_notification
setup_alert_policy = finops_infra_toolset.setup_alert_policy
schedule_audit = finops_infra_toolset.schedule_audit
delete_resource = finops_infra_toolset.delete_resource

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
    tools=[finops_infra_toolset],
)
