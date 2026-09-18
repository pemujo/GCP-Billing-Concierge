import argparse
import os
from pathlib import Path
import re
import time
from typing import List, Union

from dotenv import load_dotenv
from google.api_core import exceptions
from google.cloud import bigquery, iam_admin_v1, resourcemanager_v3
from google.iam.v1 import policy_pb2

os.environ["GRPC_VERBOSITY"] = "NONE"
os.environ["GRPC_TRACE"] = ""


def draw_header(title: str, width: int = 80) -> None:
    """
    Prints a formatted header block to the console for UI clarity.

    Args:
        title (str): The text to display inside the header.
        width (int): The total character width of the header box.
    """
    print("=" * width)
    print(f"| {title}".ljust(width - 1) + "|")
    print("=" * width)


def update_env(filepath: Union[str, Path], key: str, value: str) -> None:
    """
    Updates or appends a key-value pair in a .env file and keeps root .env in sync.

    Args:
        filepath (Union[str, Path]): Path to the .env file.
        key (str): The environment variable name.
        value (str): The value to assign to the key.
    """
    targets = {Path(filepath), Path(".env")}
    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            lines = []
            if target.exists():
                with open(target, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            lines = [line for line in lines if not line.startswith(f"{key}=")]
            lines.append(f"{key}={value}\n")
            with open(target, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            print(f"⚠️ Warning: Could not update {target}: {e}")



def create_service_account(
    project_id: str, sa_id: str, display_name: str = "GCP Billing Concierge"
) -> str:
    """
    Creates a Google Cloud Service Account if it does not already exist.

    Args:
        project_id (str): The GCP Project ID where the SA will be created.
        sa_id (str): The account ID (prefix) for the service account.
        display_name (str): Human-readable display name for the service account.

    Returns:
        str: The full email address of the created or existing service account.
    """
    client = iam_admin_v1.IAMClient()
    project_path = f"projects/{project_id}"
    sa_email = f"{sa_id}@{project_id}.iam.gserviceaccount.com"
    sa_resource_name = f"projects/{project_id}/serviceAccounts/{sa_email}"

    try:
        print(f"-> Creating Service Account: {sa_id} ({display_name})...")
        client.create_service_account(
            request={
                "name": project_path,
                "account_id": sa_id,
                "service_account": {
                    "display_name": display_name
                },
            }
        )
    except exceptions.AlreadyExists:
        print(f"  (Note: Service account {sa_id} already exists)")

    # Wait for IAM Propagation
    max_retries = 5
    wait_interval = 3  # seconds
    time.sleep(1)
    print("⏳ Waiting for IAM propagation...")

    for i in range(max_retries):
        try:
            client.get_service_account(request={"name": sa_resource_name})
            print("  🚀 Service account is now active and ready.")
            return sa_email
        except (exceptions.NotFound, exceptions.InvalidArgument):
            if i < max_retries - 1:
                print(f"  ...still propagating (attempt {i+1}/{max_retries})...")
                time.sleep(wait_interval)
            else:
                raise RuntimeError(
                    f"❌ Timeout: Service account {sa_email} failed to propagate."
                ) from None
        except Exception as e:
            if i < max_retries - 1:
                print(f"  ⚠️ Unexpected error, retrying: {e}")
                time.sleep(wait_interval)
            else:
                raise RuntimeError("❌ Unexpected error during IAM propagation.") from e

    return sa_email


def add_iam_member(project_id: str, roles: List[str], member: str) -> None:
    """
    Grants a list of IAM roles to a specific member for a given project.

    Args:
        project_id (str): The GCP Project ID where roles are being granted.
        roles (List[str]): A list of GCP role strings (e.g., 'roles/viewer').
        member (str): The member identifier (e.g., 'serviceAccount:email@project.com').
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
        print("  (Note: All project roles already assigned)")


def grant_sa_user_role_on_self(project_id: str, sa_email: str) -> None:
    """
    Grants the 'Service Account User' role to the service account on its own 
    resource. This is required for Cloud Scheduler or other services to 'act as' 
    this specific account.
    """
    client = iam_admin_v1.IAMClient()
    resource = f"projects/{project_id}/serviceAccounts/{sa_email}"
    member = f"serviceAccount:{sa_email}"
    role = "roles/iam.serviceAccountUser"

    print(f"-> Granting {role} to {sa_email} on its own resource...")

    try:
        policy = client.get_iam_policy(request={"resource": resource})
        binding = next((b for b in policy.bindings if b.role == role), None)
        if binding:
            if member in binding.members:
                print("  (Note: SA User role already assigned on self)")
                return
            binding.members.append(member)
        else:
            new_binding = policy_pb2.Binding(role=role, members=[member])
            policy.bindings.append(new_binding)

        client.set_iam_policy(request={"resource": resource, "policy": policy})
        print("  ✅ Successfully granted self-user permissions.")
    except Exception as e:
        print(f"  ⚠️ Warning: Failed to grant self-user role: {e}")


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
    """
    client = bigquery.Client(project=project_id)
    table_ref = client.dataset(dataset_id).table(table_id)

    print(f"-> Granting {role} specifically on table: {project_id}.{dataset_id}.{table_id}...")

    policy = client.get_iam_policy(table_ref)
    binding = next((b for b in policy.bindings if b["role"] == role), None)

    if binding:
        if member in binding["members"]:
            print("  (Note: Member already has access to this table)")
            return

        if isinstance(binding["members"], list):
            binding["members"].append(member)
        else:
            binding["members"].add(member)
    else:
        policy.bindings.append({
            "role": role,
            "members": [member],
        })

    client.set_iam_policy(table_ref, policy)
    print("  ✅ Table IAM permissions configured.")


def main() -> None:
    """
    Orchestrates the provisioning of the FinOps Agent service account and permissions.
    """
    parser = argparse.ArgumentParser(description="Provision Service Account for FinOps Agent")
    parser.add_argument("--agent-name", default=None, help="Agent name")
    args, _ = parser.parse_known_args()

    agent_env = Path("GCP_billing_concierge/.env")
    load_dotenv(agent_env)
    if Path(".env").exists():
        load_dotenv(Path(".env"))

    local_project = os.getenv("GOOGLE_CLOUD_PROJECT")
    billing_project = os.getenv("BILLING_EXPORT_PROJECT_ID")
    billing_dataset = os.getenv("BILLING_EXPORT_DATASET")
    billing_table = os.getenv("BILLING_EXPORT_TABLE")

    if not local_project:
        print("❌ ERROR: Missing GOOGLE_CLOUD_PROJECT in .env.")
        return

    raw_agent_name = (
        args.agent_name
        or os.getenv("AGENT_NAME", "GCP_billing_concierge").strip()
    )
    clean_agent_name = (
        re.sub(r"[^a-zA-Z0-9-]", "-", raw_agent_name).lower().strip("-")
    )
    if clean_agent_name in ("gcp-billing-concierge", "gcp_billing_concierge", ""):
        default_sa_id = "gcp-billing-concierge-sa"
    else:
        candidate_sa = f"{clean_agent_name}-sa"
        if len(candidate_sa) > 30:
            candidate_sa = candidate_sa[:30].rstrip("-")
        if len(candidate_sa) < 6:
            candidate_sa = f"{candidate_sa}-agent"[:30]
        default_sa_id = candidate_sa

    existing_sa = os.getenv("AGENT_SERVICE_ACCOUNT", "").strip()
    if existing_sa and "@" in existing_sa:
        sa_id = existing_sa.split("@")[0]
    else:
        sa_id = default_sa_id

    clean_disp = raw_agent_name.replace("-", " ").replace("_", " ").strip()
    sa_display_name = (
        clean_disp.title()
        if clean_disp.lower() not in ("gcp billing concierge", "gcp_billing_concierge", "")
        else "GCP Billing Concierge"
    )

    sa_member = f"serviceAccount:{sa_id}@{local_project}.iam.gserviceaccount.com"

    draw_header("🔑 Provisioning Agent Service Account")

    # 1. Create the SA
    email_address = create_service_account(
        local_project, sa_id, display_name=sa_display_name
    )

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
    grant_sa_user_role_on_self(local_project, email_address)

    # 3. Check BigQuery Access Mode (OAuth vs Default Credentials)
    enable_oauth_val = os.getenv("ENABLE_USER_OAUTH", "").strip().lower()
    if not enable_oauth_val:
        print("\nBigQuery Authentication Mode:")
        print("  1) oauth [Default - Recommended] (End-user OAuth delegation; SA skips BQ data read permissions)")
        print("  2) default_credentials (Service account needs table-level 'roles/bigquery.dataViewer')")
        try:
            raw_choice = input("Enable User OAuth or use Default Credentials? [1-2, default: 1 (oauth)]: ").strip().lower()
        except EOFError:
            raw_choice = ""
        is_oauth = raw_choice not in ("2", "default_credentials", "default")
    else:
        is_oauth = enable_oauth_val in ("true", "1", "yes")

    if is_oauth:
        print("\nℹ️ BigQuery Data Viewer table IAM skipped on service account.")
        print("   (User OAuth is enabled; BigQuery queries execute under each chatting user's delegated identity).")
        update_env(agent_env, "ENABLE_USER_OAUTH", "true")
        update_env(agent_env, "REQUIRE_USER_OAUTH", "true")
    else:
        print("\n🚀 Granting BigQuery Data Viewer on billing export table to service account...")
        if billing_project and billing_dataset and billing_table:
            add_bigquery_table_iam_member(
                billing_project,
                billing_dataset,
                billing_table,
                "roles/bigquery.dataViewer",
                sa_member,
            )
        else:
            print("⚠️ Warning: Billing table coordinates (BILLING_EXPORT_PROJECT_ID, DATASET, TABLE) missing in .env.")
        update_env(agent_env, "ENABLE_USER_OAUTH", "false")
        update_env(agent_env, "REQUIRE_USER_OAUTH", "false")

    # 4. Update .env
    update_env(agent_env, "AGENT_SERVICE_ACCOUNT", email_address)

    print("-" * 80)
    print("✅ Provisioning Complete!")


if __name__ == "__main__":
    main()
