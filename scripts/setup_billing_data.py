import os
from pathlib import Path
from typing import Any, Dict, Union

from dotenv import load_dotenv
from google.api_core import exceptions
from google.cloud import bigquery


def draw_header(title: str, width: int = 80) -> None:
    """
    Draws a formatted header box for the CLI to separate setup sections.

    Args:
        title (str): The text to display in the header.
        width (int): Total character width of the box. Defaults to 80.

    Returns:
        None: Prints to stdout.
    """
    print("=" * width)
    print(f"| {title}".ljust(width - 1) + "|")
    print("=" * width)


def update_env(filepath: Union[str, Path], new_vars: Dict[str, Any]) -> None:
    """
    Updates or appends variables in the .env file while preserving 
    existing keys.

    Args:
        filepath (Union[str, Path]): Path to the .env file to be modified.
        new_vars (Dict[str, Any]): A dictionary of key-value pairs to write 
        to the file.

    Returns:
        None: Modifies the file on disk.
    """
    lines = []
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            lines = f.readlines()

    # Filter out lines we are about to update
    lines = [
        line
        for line in lines
        if not any(line.startswith(f"{k}=") for k in new_vars.keys())
    ]

    # Append new values
    for k, v in new_vars.items():
        lines.append(f"{k}={v}\n")

    with open(filepath, "w") as f:
        f.writelines(lines)


def main() -> None:
    """
    CLI utility to configure GCP Billing data access for the FinOps Agent.
    
    It allows the user to either point to an existing BigQuery billing export 
    or setup a mock dataset with sample data for development purposes. 
    The configuration is saved to a local .env file.

    Returns:
        None: Entry point for the setup script.
    """
    agent_env = Path("Cloud_AI_FinOps_Agent/.env")

    # Load existing .env to use as defaults
    load_dotenv(agent_env)

    # Context variables from .env or Shell
    curr_execution_p = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    curr_billing_p = os.getenv("BILLING_EXPORT_PROJECT_ID", "")
    curr_d = os.getenv("BILLING_EXPORT_DATASET", "")
    curr_t = os.getenv("BILLING_EXPORT_TABLE", "")

    draw_header("💰 GCP Billing Data Setup")
    print("1) Use an existing Billing export on BigQuery table.")
    print("2) Load sample file (Recommended for Dev).")

    choice = input("\nSelect option [1-2]: ")

    # 1. Determine the Execution Project (Where the Agent Runs)
    exec_p = (
        input(
        f"Enter Execution Project ID (where agent runs) [{curr_execution_p}]: "
        )
        or curr_execution_p
    )

    if not exec_p:
        print("❌ Error: Execution Project ID is required.")
        return

    final_vars = {"GOOGLE_CLOUD_PROJECT": exec_p}

    if choice == "2":
        draw_header("OPTION 2: SAMPLE DATA SETUP")
        # For sample setup, billing and execution share the same project
        dataset_id = input("Dataset name [billing_test]: ") or "billing_test"
        table_id = "billing_sample_table"

        client = bigquery.Client(project=exec_p)
        dataset_ref = bigquery.DatasetReference(exec_p, dataset_id)

        try:
            client.get_dataset(dataset_ref)
        except exceptions.NotFound:
            print(f"Creating dataset {dataset_id}...")
            client.create_dataset(bigquery.Dataset(dataset_ref))

        table_ref = dataset_ref.table(table_id)
        schema = client.schema_from_json("./mock_data/billing_schema.json")

        job_config = bigquery.LoadJobConfig(
            schema=schema,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            time_partitioning=bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="ingestion_time",
            ),
        )

        print(f"Loading sample data into {exec_p}:{dataset_id}.{table_id}...")
        try:
            with open(
                "./mock_data/billing_export_test_table.json", "rb"
            ) as source_file:
                load_job = client.load_table_from_file(
                    source_file, table_ref, job_config=job_config
                )
            load_job.result()
            print(f"✅ Successfully loaded {load_job.output_rows} rows.")
        except Exception as e:
            print(f"❌ BigQuery Load Failed: {e}")
            return

        final_vars.update(
            {
                "BILLING_EXPORT_PROJECT_ID": exec_p,
                "BILLING_EXPORT_DATASET": dataset_id,
                "BILLING_EXPORT_TABLE": table_id,
            }
        )

    else:
        draw_header("OPTION 1: EXISTING BILLING EXPORT SETUP")
        # For production, the billing project might be different project
        bq_project = (
            input(f"Existing Billing Project ID [{curr_billing_p}]: ")
            or curr_billing_p
        )
        bq_dataset = input(f"Dataset name [{curr_d}]: ") or curr_d
        bq_table = input(f"Table name [{curr_t}]: ") or curr_t

        final_vars.update(
            {
                "BILLING_EXPORT_PROJECT_ID": bq_project,
                "BILLING_EXPORT_DATASET": bq_dataset,
                "BILLING_EXPORT_TABLE": bq_table,
            }
        )

    # Persist all variables to the agent .env
    update_env(agent_env, final_vars)
    print("-" * 80)
    print(f"✅ Setup Complete! Agent environment updated: {agent_env}")


if __name__ == "__main__":
    main()