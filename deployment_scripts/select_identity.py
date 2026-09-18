#!/usr/bin/env python3
"""
Interactive prompt to select the Agent Identity strategy for GCP Billing Concierge.
Defaults to 'agent_identity' for improved, keyless security.
Uses standard library only for zero-dependency execution.
"""

import os
from pathlib import Path
import re
from typing import Any, Dict, Union


def draw_header(title: str, width: int = 80) -> None:
    """Prints a formatted banner header."""
    print("=" * width)
    print(f"| {title}".ljust(width - 1) + "|")
    print("=" * width)


def read_env_file(filepath: Path) -> Dict[str, str]:
    """Reads key-value pairs from a .env file without external dependencies."""
    env = {}
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def update_env(filepath: Union[str, Path], new_vars: Dict[str, Any]) -> None:
    """Updates or appends variables in .env while keeping root .env and subfolder in sync."""
    targets = {Path(filepath), Path(".env"), Path("GCP_billing_concierge/.env")}
    for target in targets:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            lines = []
            if target.exists():
                with open(target, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            lines = [
                line
                for line in lines
                if not any(line.startswith(f"{k}=") for k in new_vars.keys())
            ]
            for k, v in new_vars.items():
                lines.append(f"{k}={v}\n")

            with open(target, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception:
            pass


def main() -> None:
    agent_env = Path("GCP_billing_concierge/.env")
    root_env = Path(".env")
    env_vars = {}
    if agent_env.exists():
        env_vars = read_env_file(agent_env)
    elif root_env.exists():
        env_vars = read_env_file(root_env)

    # Check current .env settings to select default option 1-4
    env_id_type = env_vars.get("IDENTITY_TYPE", os.getenv("IDENTITY_TYPE", "agent_identity")).strip()
    env_oauth = env_vars.get("ENABLE_USER_OAUTH", os.getenv("ENABLE_USER_OAUTH", "true")).strip()
    is_oauth = env_oauth.lower() not in ("false", "0", "no")

    raw_agent_name = env_vars.get("AGENT_NAME", os.getenv("AGENT_NAME", "GCP_billing_concierge")).strip()
    clean_agent_name = re.sub(r"[^a-zA-Z0-9-]", "-", raw_agent_name).lower().strip("-")
    if clean_agent_name in ("gcp-billing-concierge", "gcp_billing_concierge", ""):
        default_sa_prefix = "gcp-billing-concierge-sa"
    else:
        cand_sa = f"{clean_agent_name}-sa"
        if len(cand_sa) > 30:
            cand_sa = cand_sa[:30].rstrip("-")
        if len(cand_sa) < 6:
            cand_sa = f"{cand_sa}-agent"[:30]
        default_sa_prefix = cand_sa

    existing_sa = env_vars.get("AGENT_SERVICE_ACCOUNT", os.getenv("AGENT_SERVICE_ACCOUNT", "")).strip()
    sa_display = existing_sa if existing_sa else f"{default_sa_prefix}@<project>"

    if env_id_type == "service_account":
        default_choice = 2 if is_oauth else 4
    else:
        default_choice = 1 if is_oauth else 3

    draw_header("🔐 Select Security & Identity Strategy")
    print("Choose the identity and BigQuery authorization model for your agent:\n")
    print(f"  1) Option 1: Agent Identity with OAuth [Recommended]{' [Current / Default]' if default_choice == 1 else ''}")
    print("     • Keyless Native Agent Identity (no service account keys to manage or rotate).")
    print("     • BigQuery queries execute under each end user's personal identity via OAuth 2.0.")
    print("     • True Zero-Trust: Agent container does NOT receive BigQuery data read permissions.\n")

    print(f"  2) Option 2: Service Account with OAuth{' [Current]' if default_choice == 2 else ''}")
    print(f"     • Traditional Google Cloud Service Account ({sa_display}).")
    print("     • BigQuery queries execute under each end user's personal identity via OAuth 2.0.")
    print("     • Service account does NOT receive BigQuery data read permissions.\n")

    print(f"  3) Option 3: Agent Identity with Agent's credentials{' [Current]' if default_choice == 3 else ''}")
    print("     • Keyless Native Agent Identity (no service account keys to manage or rotate).")
    print("     • Agent queries BigQuery directly using its own ambient agent identity.")
    print("     • Grants 'roles/bigquery.dataViewer' on billing export table to agent identity.")
    print("     • End users do not need individual BigQuery permissions.\n")

    print(f"  4) Option 4: Service Account with Account's credentials [Least Recommended]{' [Current]' if default_choice == 4 else ''}")
    print(f"     • Traditional Google Cloud Service Account ({sa_display}).")
    print("     • Agent queries BigQuery directly using the service account's ambient credentials.")
    print("     • Grants 'roles/bigquery.dataViewer' on billing export table to service account.")
    print("     • End users do not need individual BigQuery permissions.\n")

    prompt = f"Select option [1-4, default: {default_choice}]: "
    try:
        raw_choice = input(prompt).strip()
    except EOFError:
        raw_choice = ""

    choice = raw_choice if raw_choice in ("1", "2", "3", "4") else str(default_choice)

    if choice == "1":
        id_type = "agent_identity"
        enable_oauth = "true"
        desc = "Option 1: Agent Identity with OAuth (Recommended - Keyless Zero-Trust)"
    elif choice == "2":
        id_type = "service_account"
        enable_oauth = "true"
        desc = "Option 2: Service Account with OAuth (Zero-Trust User Delegation)"
    elif choice == "3":
        id_type = "agent_identity"
        enable_oauth = "false"
        desc = "Option 3: Agent Identity with Agent's credentials (Ambient Identity)"
    else:  # choice == "4"
        id_type = "service_account"
        enable_oauth = "false"
        desc = "Option 4: Service Account with Account's credentials (Ambient Service Account)"

    update_env(
        agent_env,
        {
            "IDENTITY_TYPE": id_type,
            "ENABLE_USER_OAUTH": enable_oauth,
            "REQUIRE_USER_OAUTH": enable_oauth,
        },
    )

    print(f"✅ Configured: {desc}")
    print(f"   • IDENTITY_TYPE={id_type}")
    print(f"   • ENABLE_USER_OAUTH={enable_oauth}")
    print(f"   • REQUIRE_USER_OAUTH={enable_oauth} (saved to .env)\n")


if __name__ == "__main__":
    main()
