# 💰 Billing Concierge Agent
An intelligent automation agent that simplifies Google Cloud cost management. Starting with answering billing questions, this agent also acts as a FinOps concierge—capable of auditing environments, detecting anomalies, and provisioning monitoring infrastructure using natural language. Built with Google ADK, Gemini, and deployed via `google-agents-cli` on Agent Runtime (Gemini Enterprise Agent Platform).

🛠️ Integrated Cloud Ecosystem:
The agent seamlessly orchestrates the following Google Cloud services:
- **BigQuery**: Analysis of Standard/Detailed Billing Exports.
- **Cloud Scheduler**: Automation of recurring cost audits and reporting.
- **Cloud Monitoring**: Dynamic provisioning of Alert Policies and Notification Channels.
- **Cloud Logging**: Recording of detected billing anomalies.
- **Secret Manager**: Secure management of Agent metadata and IDs.

---

## 📋 Prerequisites
Before running the setup, ensure you have:

* **Python 3.11+** and **uv package manager** [(uv Installation guide)](https://docs.astral.sh/uv/getting-started/installation/)
* **gcloud CLI** installed and authenticated. [(Google SDK Installation guide)](https://docs.cloud.google.com/sdk/docs/install-sdk)
* **Application Default Credentials** set with `gcloud auth application-default login`.
* **Google Agents CLI & ADK** installed via `uvx google-agents-cli setup` [(ADK Docs)](https://adk.dev/get-started/installation/)
* Optional: **Gemini Enterprise application** with Gemini Enterprise or Business licenses. [(Gemini Enterprise Quickstart Guide)](https://docs.cloud.google.com/gemini/enterprise/docs/quickstart-gemini-enterprise).

### 📊 Billing Export Setup
**Recommended:** Enabling a BigQuery billing export is a highly common and recommended FinOps best practice. The GCP Billing Concierge relies on this billing export for live querying.
If you do not have an existing export, the setup script (`make install`) includes an option to generate a sample dataset for testing purposes.

- [Official Documentation: Set up Cloud Billing data export to BigQuery](https://cloud.google.com/billing/docs/how-to/export-data-bigquery)
- **Key Requirement:** You must have the `Billing Account Administrator` role on the Cloud Billing account to enable this export.
- **Latency Note:** Once enabled, it can take 24 to 48 hours for the first data points to appear in BigQuery.

---

## 📂 Project Structure

```text
.
├── GCP_billing_concierge /            # Root directory for Agent Logic
│   ├── agent.py                       # Main Orchestrator Agent (Billing Concierge)
│   ├── prompt.py                      # Orchestrator system instructions
│   ├── .env.example                   # Environment variable template
│   ├── skills/                        # ADK Agent Skills (analysis, alerting, scheduling)
│   ├── tools/
│   │   ├── finops_bigquery_toolset.py # BigQuery FinOps Toolset (location recovery & budget limits)
│   │   └── tools.py                   # Anomaly and audit logging tools
│   └── sub_agents/
│       └── finops_infra_agent/        # Specialized agent for Platform Ops
│           ├── agent.py               # Sub-agent: Handles infrastructure tasks
│           ├── prompt.py              # Sub-agent: Instructions for CRON and Monitoring
│           └── tools/tools.py         # Custom tools (Scheduler, Alerts, Notifications)
├── deployment_scripts/                
│   ├── setup_billing_data.py          # Configures BQ dataset (Real or Mock)
│   └── create_sa.py                   # Provisions Agent Service Account & IAM roles
├── mock_data/  
│   ├── billing_export_test_table.json # Sample billing dataset
│   └── billing_schema.json            # Sample billing dataset schema
├── new_agent_evals/                   # Modern ADK evaluation benchmark suite
│   ├── billing_eval_dataset_fully_modern.evalset.json
│   └── eval_config.json
├── agents-cli-manifest.yaml           # Manifest for google-agents-cli lifecycle
├── pyproject.toml                     # Project dependencies & configuration
├── Makefile                           # Streamlined automation (install, playground, deploy, eval)
└── README.md
```

### Agent Architecture
![Agent Architecture](agent_pattern.png)

---

## 🔑 Permissions & Roles Used

### For the Admin (You)
* `roles/resourcemanager.projectIamAdmin`: To manage Service Account roles.
* `roles/iam.serviceAccountAdmin`: To create the agent identity.
* `roles/bigquery.admin`: To configure datasets and verify schemas.
* `roles/aiplatform.admin`: To deploy agent to Agent Runtime.
* `roles/secretmanager.admin`: To create and manage the Agent ID secret.
* `roles/bigquery.dataViewer`: Minimum access needed to the existing **Billing Export table**.

### For the Agent (Service Account)
The `make install` script creates `gcp-billing-concierge-sa` and grants:

**Agent Project (Local Execution & Infra Management):**
* BigQuery: `roles/bigquery.jobUser` (To run analysis jobs).
* AI & Platform: `roles/aiplatform.user` and `roles/geminidataanalytics.dataAgentStatelessUser`.
* Infrastructure Ops: 
  * `roles/cloudscheduler.admin`: To manage recurring audit schedules.
  * `roles/monitoring.alertPolicyEditor`: To create and edit billing alerts.
  * `roles/monitoring.notificationChannelEditor`: To manage email notification targets.
* Logging: `roles/logging.logWriter` and `roles/logging.configWriter` (For anomaly logging).
* Secrets: `roles/secretmanager.secretAccessor` (To retrieve its own Agent ID).
* Utility: `roles/serviceusage.serviceUsageConsumer` and `roles/telemetry.writer`.
* Identity: `roles/iam.serviceAccountUser` (Self-assigned so Cloud Scheduler can trigger the agent).

**Billing Project (Data Access):**
* `roles/bigquery.dataViewer`: Granted strictly at the table level for the Billing Export data.

---

## 🚀 Quickstart & Usage

There are two primary ways to deploy and use the agent:

1. **Fast Track**: Scaffold and deploy using `google-agents-cli`.
2. **Custom / Clone**: Clone this repository for development, customization, and deployment.

---

### Method 1: Using `google-agents-cli` (Fast Track)

#### Step 1: Install `google-agents-cli` & Authenticate
```bash
uvx google-agents-cli setup
gcloud auth application-default login
```

#### Step 2: Create Project and Run Setup Wizard
```bash
export AGENT_NAME=billing-concierge-${RANDOM}
uvx google-agents-cli create ${AGENT_NAME} -d agent_runtime -a pemujo/GCP-Billing-Concierge
cd ${AGENT_NAME}
make install
```
`make install` runs an interactive configuration wizard that guides you through selecting your GCP project and BigQuery billing export details, enables all required Cloud APIs, provisions the Agent Service Account, and automatically writes your `.env` configuration file.

#### Step 3: Test Locally in the Agent Playground
Start the local interactive playground:
```bash
make playground
```

#### Step 4: Deploy to Agent Runtime
Deploy the agent to managed cloud infrastructure:
```bash
make deploy
```

---

### Method 2: GitHub Clone and Deploy (Development Flow)

#### Step 1: Clone Repository & Install Dependencies
```bash
git clone https://github.com/pemujo/GCP-Billing-Concierge.git
cd GCP-Billing-Concierge

# Install dependencies with uv
uv sync
```

#### Step 2: Interactive Provisioning & Configuration (`make install`)
Run the automated installation wizard:
```bash
make install
```

> [!TIP]
> **Automatic `.env` Generation:** You do not need to manually create or configure `.env` beforehand! `make install` interactively prompts for your settings and automatically writes `GCP_billing_concierge/.env`:
> 1. **Execution Project (`GOOGLE_CLOUD_PROJECT`)**: Prompts for your agent project ID (defaults to your active `gcloud` project).
> 2. **API Activation**: Enables all required Google Cloud APIs (`aiplatform`, `bigquery`, `logging`, `cloudscheduler`, `secretmanager`, etc.).
> 3. **Billing Data Source Configuration**:
>    - **Option 1 (Existing Billing Export)**: Prompts for your BigQuery coordinates (`BILLING_EXPORT_PROJECT_ID`, `BILLING_EXPORT_DATASET`, `BILLING_EXPORT_TABLE`).
>    - **Option 2 (Sample Data)**: Automatically provisions a mock BigQuery dataset and loads sample GCP billing records for sandbox testing.
> 4. **Service Account Provisioning**: Creates `gcp-billing-concierge-sa`, assigns least-privilege IAM roles across execution and billing projects, and saves `AGENT_SERVICE_ACCOUNT` into `.env`.
>
> *(Optional: If you prefer to pre-populate `.env` by copying `GCP_billing_concierge/.env.example`, `make install` will read existing values and offer them as default suggestions in brackets).*

#### Step 3: Test Locally in the Agent Playground
Start the local Agent Playground web interface:
```bash
make playground
```
This launches the local Agent Dev-UI at `http://127.0.0.1:8080/dev-ui/?app=GCP_billing_concierge`, featuring:
* **Interactive Multi-Turn Chat**: Full conversational session with memory and context retention.
* **Trace & Tool Execution Viewer**: Real-time inspection of generated BigQuery SQL, dry-run scan estimates, and sub-agent tool calls.
* **Live Reloading**: Hot-reloads your agent when code or prompts change.

#### Step 4: Deploy to Agent Runtime
Deploy the agent to managed cloud infrastructure:
```bash
make deploy
```

Upon successful deployment:
* `agents-cli deploy` packages the agent and deploys to Agent Runtime.
* Automatically extracts the deployed Agent ID from `deployment_metadata.json` and syncs it to Secret Manager (`billing-concierge-agent-id`).
* Scheduled audits provisioned by `finops_infra_agent` automatically invoke the live Agent Runtime endpoint.

#### Step 5: Run Evaluation Benchmarks
Benchmark agent accuracy against the 50-case golden dataset:
```bash
make eval
```

#### Step 6: Register with Gemini Enterprise (Optional)
Register your deployed agent so users can discover and interact with it in Gemini Enterprise using `google-agents-cli`:
```bash
uvx google-agents-cli publish gemini-enterprise --interactive
```

---

## 📝 Disclaimer
This agent sample is provided for illustrative purposes only and is not intended for production use. It serves as a foundational starting point for teams to develop their own agents.

Users are responsible for the development, testing, and security hardening of any agents derived from this sample.
