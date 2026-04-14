import os
from pathlib import Path
from typing import List, Union

from dotenv import load_dotenv
from google.api_core import exceptions
from google.cloud import iam_admin_v1, resourcemanager_v3
from google.iam.v1 import policy_pb2

os.environ["GRPC_VERBOSITY"] = "NONE"
os.environ["GRPC_TRACE"] = ""


def draw_header(title: str, width: int = 80) -> None:
    """
    Prints a formatted header block to the console for UI clarity.

    Args:
        title (str): The text to display inside the header.
        width (int): The total character width of the header box.

    Returns:
        None: This function prints to stdout and does not return a value.
    """
    print("=" * width)
    print(f"| {title}".ljust(width - 1) + "|")
    print("=" * width)


def update_env(filepath: Union[str, Path], key: str, value: str) -> None:
    """
    Updates or appends a key-value pair in a .env file.

    Args:
        filepath (Union[str, Path]): Path to the .env file.
        key (str): The environment variable name.
        value (str): The value to assign to the key.

    Returns:
        None: This function modifies a file on disk and does not return a value.
    """
    lines = []
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            lines = f.readlines()
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}\n")
    with open(filepath, "w") as f:
        f.writelines(lines)


def create_service_account(project_id: str, sa_id: str) -> str:
    """
    Creates a Google Cloud Service Account if it does not already exist.

    Args:
        project_id (str): The GCP Project ID where the SA will be created.
        sa_id (str): The account ID (prefix) for the service account.

    Returns:
        str: The full email address of the created or existing service account
             (e.g., 'name@project.iam.gserviceaccount.com').
    """
    client = iam_admin_v1.IAMClient()
    project_path = f"projects/{project_id}"
    sa_email = f"{sa_id}@{project_id}.iam.gserviceaccount.com"

    try:
        print(f"-> Creating Service Account: {sa_id}...")
        client.create_service_account(
            request={
                "name": project_path,
                "account_id": sa_id,
                "service_account": {
                    "display_name": "Cloud AI FinOps Agent SA"
                },
            }
        )
    except exceptions.AlreadyExists:
        print(f"  (Note: Service account {sa_id} already exists)")

    return sa_email


def add_iam_member(project_id: str, roles: List[str], member: str) -> None:
    """
    Grants a list of IAM roles to a specific member for a given project.

    Args:
        project_id (str): The GCP Project ID where roles are being granted.
        roles (List[str]): A list of GCP role strings (e.g., 'roles/viewer').
        member (str): The member identifier 
        (e.g., 'serviceAccount:email@project.com').

    Returns:
        None: This function makes API calls to update IAM policy and 
        does not return a value.
    """
    client = resourcemanager_v3.ProjectsClient()
    project_path = f"projects/{project_id}"

    policy = client.get_iam_policy(request={"resource": project_path})
    changed = False

    for role in roles:
        print(f"-> Granting {role} in project: {project_id}...")
        binding = next((b for b in policy.bindings if b.role == role), None)

        if binding:
            if member not in binding.members:
                binding.members.append(member)
                changed = True
        else:
            new_binding = policy_pb2.Binding(role=role, members=[member])
            policy.bindings.append(new_binding)
            changed = True

    if changed:
        client.set_iam_policy(
            request={"resource": project_path, "policy": policy}
        )
    else:
        print("  (Note: All roles already assigned)")

def add_bigquery_table_iam_member(
    project_id: str, dataset_id: str, table_id: str, role: str, member: str
) -> None:
    """
    Grants an IAM role to a member specifically for a BigQuery table.

    Args:
        project_id (str): Project ID where the table exists.
        dataset_id (str): Dataset ID.
        table_id (str): Table ID.
        role (str): The role to grant (e.g., 'roles/bigquery.dataViewer').
        member (str): The member identifier.

    Returns:
        None: Updates table-level IAM policy.
    """
    client = bigquery.Client(project=project_id)
    table_ref = f"{project_id}.{dataset_id}.{table_id}"
    
    print(f"-> Granting {role} specifically on table: {table_ref}...")
    
    policy = client.get_iam_policy(table_ref)
    
    # Check if member already in binding
    binding = next((b for b in policy.bindings if b.role == role), None)
    
    if binding:
        if member in binding.members:
            print(f"  (Note: Member already has access to this table)")
            return
        binding.members.append(member)
    else:
        policy.bindings.append({"role": role, "members": {member}})
        
    client.set_iam_policy(table_ref, policy)

def main() -> None:
    """
    Orchestrates the provisioning of the FinOps Agent.

    Flow:
    1. Loads project settings from .env.
    2. Provisions the Service Account.
    3. Assigns local and cross-project permissions.
    4. Records the SA email back to the .env file.

    Returns:
        None: Execution entry point.
    """
    agent_env = Path("Cloud_AI_FinOps_Agent/.env")
    load_dotenv(agent_env)

    local_project = os.getenv("GOOGLE_CLOUD_PROJECT")
    billing_project = os.getenv("BILLING_EXPORT_PROJECT_ID")
    billing_dataset = os.getenv("BILLING_EXPORT_DATASET")
    billing_table = os.getenv("BILLING_EXPORT_TABLE")

    if not all([local_project, billing_project, billing_dataset, billing_table]):
            print("❌ ERROR: Missing project or table variables in .env.")
            return

    sa_id = "cloud-ai-finops-agent-sa"
    sa_member = (
        f"serviceAccount:{sa_id}@{local_project}.iam.gserviceaccount.com"
    )

    draw_header("🔑 Provisioning Agent Service Account")

    # 1. Create the SA
    email_address = create_service_account(local_project, sa_id)

    # 2. Grant Local Execution Roles
    local_roles = [
        "roles/bigquery.jobUser",
        "roles/aiplatform.user",
        "roles/serviceusage.serviceUsageConsumer",
        "roles/geminidataanalytics.dataAgentStatelessUser",
        "roles/telemetry.writer",
        "roles/secretmanager.secretAccessor",
        "roles/monitoring.alertPolicyEditor",
        "roles/monitoring.notificationChannelEditor",
        "roles/logging.logWriter",
        "roles/logging.configWriter",
        "roles/cloudscheduler.admin",
    ]
    add_iam_member(local_project, local_roles, sa_member)

    # 3. Grant Billing Data Access to the table (It could be cross-Project)
    add_bigquery_table_iam_member(
            billing_project, 
            billing_dataset, 
            billing_table, 
            "roles/bigquery.dataViewer", 
            sa_member
        )

    # 4. Update .env
    update_env(agent_env, "AGENT_SERVICE_ACCOUNT", email_address)

    print("-" * 80)
    print("✅ Provisioning Complete!")


if __name__ == "__main__":
    main()
