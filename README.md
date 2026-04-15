# 💰 FinOps Billing Agent
This agent allows users to query Google Cloud billing and cost data using natural language. It is built with Google ADK, Vertex AI Reasoning Engine (formerly Generative AI Service Agent), and BigQuery.

## 📋 Prerequisites
Before running the setup, ensure you have:

* **Python 3.10+** and `uv` installed (`pip install uv`).
* **gcloud CLI** installed and configured.
* **Application Default Credentials** set with `gcloud auth application-default login`.
* **Google ADK** installed.
* **Agent Starter Pack** (Recommended).
* **Google Workspace** with Gemini Enterprise or Business licenses (Optional).

### 📊 Billing Export Setup
**Recommended:** Enable a BigQuery billing export to provide the agent with real data. Otherwise, the setup can create a sample table for testing.

- [Official Documentation: Set up Cloud Billing data export to BigQuery](https://cloud.google.com/billing/docs/how-to/export-data-bigquery)
- **Key Requirement:** You must have the `Billing Account Administrator` role on the Cloud Billing account to enable this export.
- **Latency Note:** Once enabled, it can take 24 to 48 hours for the first data points to appear in BigQuery. Ensure the export is "Standard" or "Detailed" for SKU-level granularity.

## 📂 Project Structure

```text
.
├── Cloud_AI_FinOps_Agent/   # Python source code for the FinOps Agent
│   ├── agent.py             # Main agent logic and definition
│   ├── prompt.py            # System prompts and instructions
│   ├── deploy_agent.py      # Script to deploy to Vertex AI Reasoning Engine
│   └── utils/
│       └── tools.py         # Custom tools for Cloud Logging
├── scripts/                 # Setup and provisioning scripts
│   ├── setup_billing_data.py # Configures BQ dataset (Real or Mock)
│   └── create_sa.py         # Provisions the Agent Service Account
├── terraform/               # Infrastructure as Code for automation
│   ├── main.tf              # Automation logic (Scheduler/Alerts)
│   ├── provider.tf          # GCS Backend & Provider config
│   └── variables.tf         # Root variable definitions
├── Makefile                 # Shortcuts for installation and deployment
└── README.md
```

## 🚀 Getting Started

### 1. Enable Required APIs
Set environment variables with your project ID and the desired name for your agent:

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export AGENT_NAME="billing-concierge"
```

Run the following command to enable the necessary Google Cloud services:

```bash
gcloud services enable \
    compute.googleapis.com \
    aiplatform.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com \
    cloudscheduler.googleapis.com \
    bigquery.googleapis.com \
    iam.googleapis.com \
    geminidataanalytics.googleapis.com \
    discoveryengine.googleapis.com \
    cloudresourcemanager.googleapis.com \
    telemetry.googleapis.com \
    --project=$GOOGLE_CLOUD_PROJECT
```

### 2. Installation & Configuration

To initialize your FinOps agent project, use the `agent-starter-pack`. This command will create a new project directory using this repository as a template, setting up the necessary structure and dependencies.

Initialize the project:

```bash
uvx agent-starter-pack@latest create ${AGENT_NAME} \
    -d agent_engine \
    -ag \
    -a pemujo/GCP-Billing-Concierge

cd ${AGENT_NAME}
```

Once initialized, run the installation script to configure your billing export source and prepare IAM permissions. This script will prompt you for your project IDs and dataset names, then create and authorize a dedicated Service Account for the agent:

```bash
make install
```

### 3. Deployment
Deploy the agent to **Vertex AI Agent Engine**:

```bash
make deploy
```
*Note: This command package the agent and registers it in the Agent Engine. It may take a few minutes to complete.*

### 4. 🤖 Automation (Terraform)
Once the agent is deployed, use Terraform to schedule daily audits and set up email alerts for billing anomalies.

1. **Initialize Terraform:**
   ```bash
   cd terraform
   terraform init
   ```

2. **Configure Variables:**
   Create a `my-ai-agent.tfvars` file:
   ```hcl
   project_id  = "your-project-id"
   region      = "us-central1"
   alert_email = "your-email@company.com"
   ```

3. **Deploy Automation:**
   ```bash
   terraform apply -var-file="my-ai-agent.tfvars"
   ```

## 🔑 Permissions & Roles

### For the Admin (Deployment User)
* `roles/resourcemanager.projectIamAdmin`: To manage Service Account roles.
* `roles/iam.serviceAccountAdmin`: To create the agent identity.
* `roles/bigquery.admin`: To configure datasets and verify schemas.

### For the Agent (Service Account)
The `make install` script creates `gcp-billing-concierge-sa` with the following:

* **Execution Project** (where the agent runs): 
  - `roles/bigquery.jobUser`
  - `roles/aiplatform.user`
  - `roles/serviceusage.serviceUsageConsumer`
  - `roles/geminidataanalytics.dataAgentStatelessUser`
  - `roles/telemetry.writer`

* **Billing Project**: 
  - `roles/bigquery.dataViewer`

## 📝 Disclaimer
This agent sample is provided for illustrative purposes only and is not intended for production use. It serves as a foundational starting point for teams to develop their own agents.

This sample has not been rigorously tested and does not include production-grade features like robust error handling, advanced security measures, or scalability optimizations. Users are solely responsible for the development, testing, and security hardening of any agents derived from this sample.