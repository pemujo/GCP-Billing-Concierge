#!/usr/bin/env python3
"""
Configures post-deployment IAM permissions for Agent Identity.
Grants application-specific roles to the agent's keyless principal:// identity.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import List, Optional, Union

from dotenv import load_dotenv
from google.api_core import exceptions
from google.cloud import bigquery, resourcemanager_v3, secretmanager
from google.iam.v1 import iam_policy_pb2, policy_pb2

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def draw_header(title: str, width: int = 80) -> None:
    """Prints a formatted banner header."""
    print("=" * width)
    print(f"| {title}".ljust(width - 1) + "|")
    print("=" * width)


def update_env(filepath: Union[str, Path], key: str, value: str) -> None:
    """Updates or appends a key-value pair in .env files."""
    targets = {Path(filepath), Path(".env"), Path("GCP_billing_concierge/.env")}
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
        except Exception:
            pass


def get_agent_resource_id(
    project_id: str,
    secret_name: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> Optional[str]:
    """Retrieves the deployed agent resource ID from CLI args, metadata, or Secret Manager."""
    for arg in sys.argv[1:]:
        if arg.strip() and not arg.startswith("-"):
            return arg.strip()

    # Check deployment_metadata.json
    for path_candidate in [
        "deployment_metadata.json",
        "../deployment_metadata.json",
        "GCP_billing_concierge/deployment_metadata.json",
    ]:
        p = Path(path_candidate)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    aid = (
                        data.get("remote_agent_runtime_id")
                        or data.get("remote_agent_engine_id")
                        or data.get("resource_name")
                        or data.get("agent_id")
                    )
                    if aid:
                        return str(aid).strip()
            except Exception as e:
                logger.debug("Failed reading %s: %s", path_candidate, e)

    # Check Secret Manager across candidate secret names
    candidate_secrets: List[str] = []
    if secret_name:
        candidate_secrets.append(secret_name.strip())

    env_sec = os.getenv("AGENT_ID_SECRET_NAME")
    if env_sec and env_sec.strip() not in candidate_secrets:
        candidate_secrets.append(env_sec.strip())

    a_name = agent_name or os.getenv("AGENT_NAME")
    if a_name:
        clean = re.sub(r"[^a-zA-Z0-9_-]", "-", a_name).lower().strip("-")
        if clean and clean not in ("gcp_billing_concierge", "gcp-billing-concierge"):
            derived = f"{clean}-agent-id"
            if derived not in candidate_secrets:
                candidate_secrets.append(derived)

    if "billing-concierge-agent-id" not in candidate_secrets:
        candidate_secrets.append("billing-concierge-agent-id")

    try:
        sm_client = secretmanager.SecretManagerServiceClient()
        for sec in candidate_secrets:
            try:
                name = f"projects/{project_id}/secrets/{sec}/versions/latest"
                resp = sm_client.access_secret_version(request={"name": name})
                secret_aid = resp.payload.data.decode("utf-8").strip()
                if secret_aid:
                    logger.info("Found Agent ID from Secret Manager secret: %s", sec)
                    return secret_aid
            except Exception:
                continue
    except Exception as e:
        logger.debug("Secret Manager lookup failed: %s", e)

    return None


def resolve_principal(project_id: str, location: str, agent_resource_id: str) -> str:
    """Resolves the principal:// identifier for the deployed agent engine."""
    # Attempt lookup via AgentPlatformClient
    try:
        from google.agents.cli._agent_platform import AgentPlatformClient
        client = AgentPlatformClient(project=project_id, location=location)
        agent = client.agent_engines.get(name=agent_resource_id)
        spec = getattr(getattr(agent, "api_resource", None), "spec", None)
        eff = getattr(spec, "effective_identity", None)
        if eff:
            return eff if eff.startswith("principal://") else f"principal://{eff}"
    except Exception as e:
        logger.debug("AgentPlatformClient lookup: %s. Using resource name derivation.", e)

    # Standard fallback format for Vertex AI Agent Identity
    clean_id = agent_resource_id
    if clean_id.startswith("principal://"):
        return clean_id
    if not clean_id.startswith("iam.googleapis.com/"):
        clean_id = f"iam.googleapis.com/{clean_id}"
    return f"principal://{clean_id}"


def add_project_iam_roles(project_id: str, roles: List[str], principal: str) -> None:
    """Grants project-level IAM roles to the principal."""
    client = resourcemanager_v3.ProjectsClient()
    project_path = f"projects/{project_id}"

    max_retries = 3
    for attempt in range(max_retries):
        try:
            policy = client.get_iam_policy(request=iam_policy_pb2.GetIamPolicyRequest(resource=project_path))
            changed = False

            for role in roles:
                binding = next((b for b in policy.bindings if b.role == role), None)
                if binding:
                    if principal not in binding.members:
                        print(f"  -> Granting {role} in project: {project_id}...")
                        binding.members.append(principal)
                        changed = True
                    else:
                        print(f"  ℹ️ Principal already has {role}")
                else:
                    print(f"  -> Granting {role} in project: {project_id}...")
                    policy.bindings.append(policy_pb2.Binding(role=role, members=[principal]))
                    changed = True

            if changed:
                client.set_iam_policy(
                    request=iam_policy_pb2.SetIamPolicyRequest(resource=project_path, policy=policy)
                )
                print(f"  ✅ Successfully updated project IAM policy.")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise e


def add_bigquery_table_iam_member(
    project_id: str, dataset_id: str, table_id: str, role: str, member: str
) -> None:
    """Grants table-level IAM role to the principal."""
    client = bigquery.Client(project=project_id)
    table_ref = client.dataset(dataset_id).table(table_id)

    print(f"  -> Granting {role} on table: {project_id}.{dataset_id}.{table_id}...")
    try:
        policy = client.get_iam_policy(table_ref)
        binding = next((b for b in policy.bindings if b.get("role") == role), None)

        if binding:
            if member in binding.get("members", []):
                print("  ℹ️ Principal already has table access.")
                return
            if isinstance(binding["members"], list):
                binding["members"].append(member)
            else:
                binding["members"].add(member)
        else:
            policy.bindings.append({"role": role, "members": [member]})

        client.set_iam_policy(table_ref, policy)
        print("  ✅ Table IAM permissions configured.")
    except Exception as e:
        print(f"  ⚠️ Warning: Could not set BigQuery table IAM policy: {e}")
        print("     Ensure your user account has 'roles/bigquery.admin' on the billing dataset.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Grant IAM roles for Agent Identity.")
    parser.add_argument("agent_resource_id", nargs="?", default=None, help="Agent resource ID")
    parser.add_argument("--agent-name", default=None, help="Agent display or service name")
    parser.add_argument("--secret-name", default=None, help="Secret Manager secret name")
    args, _ = parser.parse_known_args()

    agent_env = Path("GCP_billing_concierge/.env")
    root_env = Path(".env")
    if agent_env.exists():
        load_dotenv(agent_env)
    elif root_env.exists():
        load_dotenv(root_env)

    local_project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    region = os.getenv("GOOGLE_CLOUD_REGION", "us-central1").strip()
    billing_project = os.getenv("BILLING_EXPORT_PROJECT_ID", "").strip() or local_project
    billing_dataset = os.getenv("BILLING_EXPORT_DATASET", "").strip()
    billing_table = os.getenv("BILLING_EXPORT_TABLE", "").strip()

    if not local_project:
        print("❌ Error: GOOGLE_CLOUD_PROJECT is required in .env.")
        sys.exit(1)

    draw_header("🔐 Post-Deployment IAM Configuration for Agent Identity")

    agent_id = args.agent_resource_id
    if not agent_id:
        agent_id = get_agent_resource_id(
            local_project, secret_name=args.secret_name, agent_name=args.agent_name
        )
    if not agent_id:
        print("⚠️ Could not auto-detect deployed Agent Resource ID from metadata or secrets.")
        agent_id = input("Please enter the Agent Resource ID (e.g., projects/.../reasoningEngines/...): ").strip()

    if not agent_id:
        print("❌ Error: Agent Resource ID is required.")
        sys.exit(1)

    principal = resolve_principal(local_project, region, agent_id)
    print(f"🎯 Target Agent Resource: {agent_id}")
    print(f"🪪 Resolved Principal:    {principal}\n")

    # 1. Grant Application Roles on Execution Project
    print("🚀 [Step 1/2] Granting application roles on execution project...")
    app_roles = [
        "roles/bigquery.jobUser",
        "roles/secretmanager.secretAccessor",
        "roles/monitoring.alertPolicyEditor",
        "roles/monitoring.notificationChannelEditor",
        "roles/cloudscheduler.admin",
        "roles/logging.configWriter",
        "roles/geminidataanalytics.dataAgentStatelessUser",
        "roles/telemetry.writer",
    ]
    add_project_iam_roles(local_project, app_roles, principal)

    # 2. Check BigQuery Access Mode (OAuth vs Default Credentials)
    enable_oauth_val = os.getenv("ENABLE_USER_OAUTH", "").strip().lower()
    if not enable_oauth_val:
        print("\nBigQuery Authentication Mode:")
        print("  1) oauth [Default - Recommended] (End-user OAuth delegation; agent skips BQ data read permissions)")
        print("  2) default_credentials (Agent identity needs table-level 'roles/bigquery.dataViewer')")
        try:
            raw_choice = input("Enable User OAuth or use Default Credentials? [1-2, default: 1 (oauth)]: ").strip().lower()
        except EOFError:
            raw_choice = ""
        is_oauth = raw_choice not in ("2", "default_credentials", "default")
    else:
        is_oauth = enable_oauth_val in ("true", "1", "yes")

    if is_oauth:
        print("\nℹ️ [Step 2/2] BigQuery Data Viewer table IAM skipped on agent identity.")
        print("   (User OAuth is enabled; BigQuery queries execute under each chatting user's delegated identity).")
        update_env(agent_env, "ENABLE_USER_OAUTH", "true")
        update_env(agent_env, "REQUIRE_USER_OAUTH", "true")
    else:
        print("\n🚀 [Step 2/2] Granting BigQuery Data Viewer on billing export table to agent identity...")
        if billing_project and billing_dataset and billing_table:
            add_bigquery_table_iam_member(
                billing_project,
                billing_dataset,
                billing_table,
                "roles/bigquery.dataViewer",
                principal,
            )
        else:
            print("⚠️ Warning: Billing table coordinates (BILLING_EXPORT_PROJECT_ID, DATASET, TABLE) missing in .env.")
        update_env(agent_env, "ENABLE_USER_OAUTH", "false")
        update_env(agent_env, "REQUIRE_USER_OAUTH", "false")

    # 3. Update .env with the principal identifier
    update_env(agent_env, "AGENT_IDENTITY_PRINCIPAL", principal)

    print("\n" + "=" * 80)
    print(f"🎉 SUCCESS! Post-deployment IAM permissions configured for Agent Identity:")
    print(f"   Principal: {principal}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
