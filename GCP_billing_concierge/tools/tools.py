from datetime import date
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


def log_billing_anomaly(
    logging_client: Any,
    project_id: str,
    full_table_path: str,
    anomaly_type: str,
    severity: str,
    details: str,
    log_name: Optional[str] = None,
) -> str:
    """
    Logs a structured billing anomaly or high-cost event to Google Cloud Logging.

    This function maps human-readable or standard logging severity strings to 
    GCP-specific logging levels and writes a structured JSON payload to the 
    'billing-anomaly-detector' log. This log is intended to be picked up by 
    the alerting policies created by the FinOps sub-agent.

    Args:
        logging_client (google.cloud.logging.Client): The initialized GCP Logging client.
        project_id (str): The GCP Project ID where the log should be recorded.
        full_table_path (str): The BigQuery table path used as the data source for context.
        anomaly_type (str): The category of the anomaly (e.g., 'Sudden Spike', 'Sudden Drop', 'Vanished Service', 'New Service', 'Unauthorized Usage').
        severity (str): The input severity level ('CRITICAL', 'HIGH', 'ERROR', 'MEDIUM', 'WARNING', 'LOW', 'INFO', 'URGENT').
        details (str): A detailed description of the anomaly findings.

    Returns:
        str: A confirmation message indicating the anomaly type and the final 
             mapped severity level written to Cloud Logging, or an error description.
    """
    # Comprehensive severity mapping supporting descriptive and syslog standards
    severity_map = {
        "URGENT": "CRITICAL",
        "CRITICAL": "CRITICAL",
        "HIGH": "ERROR",
        "ERROR": "ERROR",
        "MEDIUM": "WARNING",
        "WARN": "WARNING",
        "WARNING": "WARNING",
        "LOW": "INFO",
        "INFO": "INFO",
        "NOTICE": "NOTICE",
        "DEBUG": "DEBUG",
    }

    normalized_severity = severity.strip().upper() if severity else "WARNING"
    final_severity = severity_map.get(normalized_severity, "WARNING")

    payload = {
        "message": f"FinOps Anomaly: {anomaly_type}",
        "details": details,
        "data_source": full_table_path,
        "detected_at": str(date.today()),
        "reported_severity": severity,
    }

    if not log_name:
        log_name = os.getenv("ANOMALY_LOG_NAME", "").strip()
    if not log_name:
        agent_name_env = os.getenv("AGENT_NAME", "").strip()
        if agent_name_env:
            clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", agent_name_env).lower().strip("-")
            if clean_name and clean_name not in ("gcp_billing_concierge", "gcp-billing-concierge"):
                log_name = f"{clean_name}-anomaly-detector"
    if not log_name:
        log_name = "billing-anomaly-detector"

    try:
        logging_logger = logging_client.logger(log_name)
        logging_logger.log_struct(
            payload,
            resource={
                "type": "global",
                "labels": {"project_id": project_id},
            },
            severity=final_severity,
        )
        return (
            f"Successfully recorded billing anomaly '{anomaly_type}' "
            f"with severity '{final_severity}' to log '{log_name}'."
        )
    except Exception as e:
        logger.exception("Failed to write billing anomaly log to Cloud Logging.")
        return "ERROR: Failed to record billing anomaly to Cloud Logging."
