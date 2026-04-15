from datetime import date
from typing import Any


def log_billing_anomaly(
    logging_client: Any,
    project_id: str,
    full_table_path: str,
    anomaly_type: str,
    severity: str,
    details: str,
) -> str:
    """
    Logs a structured billing anomaly or high-cost event to Google Cloud Logging.

    This function maps human-readable severity strings to GCP-specific logging levels
     and writes a structured JSON payload to the 'billing-anomaly-detector' log. This 
    log is intended to be picked up by the alerting policies created by the sub-agent.

    Args:
        logging_client (google.cloud.logging.Client): The initialized GCP Logging client.
        project_id (str): The GCP Project ID where the log should be recorded.
        full_table_path (str): The BigQuery table path used as the data source for context.
        anomaly_type (str): The category of the anomaly (e.g., 'Spike', 'Unauthorized Usage').
        severity (str): The input severity level ('HIGH', 'MEDIUM', 'LOW', or 'URGENT').
        details (str): A detailed description of the anomaly findings.

    Returns:
        str: A confirmation message indicating the anomaly type and the final 
             mapped severity level written to Cloud Logging.
    """
    logging_logger = logging_client.logger("billing-anomaly-detector")

    # Map user/agent severity strings to Cloud Logging Severity levels
    severity_map = {
        "HIGH": "ERROR",
        "MEDIUM": "WARNING",
        "LOW": "INFO",
        "URGENT": "CRITICAL",
    }

    final_severity = severity_map.get(severity.upper(), "WARNING")
    
    payload = {
        "message": f"FinOps Anomaly: {anomaly_type}",
        "details": details,
        "data_source": full_table_path,
        "detected_at": str(date.today()),
    }

    logging_logger.log_struct(
        payload,
        resource={
            "type": "global",
            "labels": {"project_id": project_id},
        },
        severity=final_severity,
    )
    
    return f"""Successfully logged {anomaly_type} 
    to project {project_id} with severity {final_severity}."""