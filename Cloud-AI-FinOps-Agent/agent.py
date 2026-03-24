import os
import google.auth
import google.cloud.logging

from google.auth.transport.requests import Request
from google.adk.agents import Agent
from google.adk.tools.data_agent.config import DataAgentToolConfig
from google.adk.tools.data_agent.credentials import DataAgentCredentialsConfig
from google.adk.tools.data_agent.data_agent_toolset import DataAgentToolset
from google.adk.tools.bigquery import BigQueryCredentialsConfig, BigQueryToolset
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode
from google.cloud.logging_v2.resource import Resource 
from google.auth.transport.requests import Request

# --- 1. AUTHENTICATION & PROJECT ID ---
credentials, detected_project_id = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
PROJECT_ID = detected_project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")

# Refresh the token immediately for the Data Agent and Logging clients
auth_request = Request()
credentials.refresh(auth_request)

# --- 2. CUSTOM LOGGING TOOL ---
def log_billing_anomaly(anomaly_type: str, severity: str, details: str):
    """
    Logs a billing anomaly. 
    Args:
        anomaly_type: Category (e.g., 'High Query Cost').
        severity: Must be one of: DEBUG, INFO, NOTICE, WARNING, ERROR, CRITICAL, ALERT, EMERGENCY.
        details: Specific info about the anomaly.
    """
    client = google.cloud.logging.Client(project=PROJECT_ID, credentials=credentials)
    logger = client.logger("billing-anomaly-detector")
    
    # 1. Map common AI/User terms to valid Cloud Logging Enums
    severity_map = {
        "HIGH": "ERROR",
        "MEDIUM": "WARNING",
        "LOW": "INFO",
        "URGENT": "CRITICAL"
    }
    
    # Standardize the input (uppercase and map if necessary)
    clean_severity = severity.upper()
    final_severity = severity_map.get(clean_severity, clean_severity)
    
    # Fallback to WARNING if the agent provides something totally weird
    valid_severities = ["DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]
    if final_severity not in valid_severities:
        final_severity = "WARNING"

    payload = {
        "message": f"Anomaly: {anomaly_type}",
        "details": details,
        "original_severity_input": severity # Keep the agent's original thought in the payload
    }
    
    resource_dict = {
        "type": "global",
        "labels": {"project_id": PROJECT_ID}
    }
    
    # 2. Use the validated 'final_severity'
    logger.log_struct(payload, resource=resource_dict, severity=final_severity)
    return f"Logged {anomaly_type} successfully with severity {final_severity}."

# --- 3. TOOLSET CONFIGURATIONS ---
da_creds = DataAgentCredentialsConfig(
    credentials=credentials,
)
da_toolset = DataAgentToolset(
    credentials_config=da_creds,
    data_agent_tool_config=DataAgentToolConfig(max_query_result_rows=100)
)

bq_creds = BigQueryCredentialsConfig(credentials=credentials)
bq_toolset = BigQueryToolset(
    credentials_config=bq_creds,
    bigquery_tool_config=BigQueryToolConfig(write_mode=WriteMode.BLOCKED)
)
# 6. AGENT DEFINITION (Updated to Gemini 2.5 Flash)
root_agent = Agent(
    name="data_bq_agent",
    model="gemini-2.5-flash", # <--- Updated model version
    instruction=(
        f"You are a FinOps expert with access to GCP's billing data avialable on project {PROJECT_ID}. "
        "Use the data agent called: 'Billing Agent' and the 'ask_data_agent' function to get billing analysis"
        "CRITICAL: If the agent responds saying there was an anomaly in the consumption, "
        "call 'log_billing_anomaly' immediately before responding to the user."
    ),
    tools=[da_toolset, bq_toolset, log_billing_anomaly],
)