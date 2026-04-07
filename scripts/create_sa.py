import os
from pathlib import Path
from dotenv import load_dotenv
from google.cloud import iam_admin_v1
from google.cloud import resourcemanager_v3
from google.iam.v1 import policy_pb2 
from google.api_core import exceptions

def draw_header(title, width=80):
    print("=" * width)
    print(f"| {title}".ljust(width - 1) + "|")
    print("=" * width)

def update_env(filepath, key, value):
    lines = []
    if os.path.exists(filepath):
        with open(filepath, "r") as f:
            lines = f.readlines()
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}\n")
    with open(filepath, "w") as f:
        f.writelines(lines)

def create_service_account(project_id, sa_id):
    client = iam_admin_v1.IAMClient()
    project_path = f"projects/{project_id}"
    sa_email = f"{sa_id}@{project_id}.iam.gserviceaccount.com"
    
    try:
        print(f"-> Creating Service Account: {sa_id}...")
        client.create_service_account(
            request={
                "name": project_path,
                "account_id": sa_id,
                "service_account": {"display_name": "Cloud AI FinOps Agent SA"},
            }
        )
    except exceptions.AlreadyExists:
        print(f"  (Note: Service account {sa_id} already exists)")
    
    return sa_email

def add_iam_member(project_id, role, member):
    client = resourcemanager_v3.ProjectsClient()
    project_path = f"projects/{project_id}"
    
    print(f"-> Granting {role} in project: {project_id}...")
    policy = client.get_iam_policy(request={"resource": project_path})
    
    binding = next((b for b in policy.bindings if b.role == role), None)
    
    if binding:
        if member in binding.members:
            return 
        binding.members.append(member)
    else:
        new_binding = policy_pb2.Binding(role=role, members=[member])
        policy.bindings.append(new_binding)
    
    client.set_iam_policy(request={"resource": project_path, "policy": policy})

def main():
    agent_env = Path("Cloud_AI_FinOps_Agent/.env")
    load_dotenv(agent_env)

    local_project = os.getenv("GOOGLE_CLOUD_PROJECT")
    billing_project = os.getenv("BILLING_EXPORT_PROJECT_ID")
    
    if not local_project or not billing_project:
        print("❌ ERROR: Missing project variables in .env. Run 'make setup_billing' first.")
        return

    sa_id = "cloud-ai-finops-agent-sa"
    sa_member = f"serviceAccount:{sa_id}@{local_project}.iam.gserviceaccount.com"

    draw_header("🔑 Provisioning Agent Service Account")
    
    # 1. Create the SA
    email_address = create_service_account(local_project, sa_id)

    # 2. Grant Local Execution Roles (Required to run the Agent and use API Quota)
    local_roles = [
        "roles/bigquery.jobUser", 
        "roles/aiplatform.user",
        "roles/serviceusage.serviceUsageConsumer",
        "roles/geminidataanalytics.dataAgentStatelessUser",
        "roles/telemetry.writer",

    ]
    for role in local_roles:
        add_iam_member(local_project, role, sa_member)

    # 3. Grant Billing Data Access (Cross-Project)
    add_iam_member(billing_project, "roles/bigquery.dataViewer", sa_member)

    # 4. Update .env
    update_env(agent_env, "AGENT_SERVICE_ACCOUNT", email_address)

    print("-" * 80)
    print("✅ Provisioning Complete! Please deploy and test to ensure the SA is active.")

if __name__ == "__main__":
    main()