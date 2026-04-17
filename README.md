# 💰 Billing Concierge Agent
An intelligent automation agent that simplifies Google Cloud cost management. Starting with answering billing questions, this agent also acts as a FinOps concierge—capable of auditing environments, detecting anomalies, and provisioning monitoring infrastructure using natural language. Built with Google ADK and Gemini on Vertex AI.

🛠️ Integrated Cloud Ecosystem:
The agent seamlessly orchestrates the following Google Cloud services:
- BigQuery: Analysis of Standard/Detailed Billing Exports.
- Cloud Scheduler: Automation of recurring cost audits and reporting.
- Cloud Monitoring: Dynamic provisioning of Alert Policies and Notification Channels.
- Cloud Logging: Recording of detected billing anomalies.
- Secret Manager: Secure management of Agent metadata and IDs.

## 📋 Prerequisites
Before running the setup, ensure you have:

* **Python 3.10+** and **uv package manager** [(uv Installation guide)](https://docs.astral.sh/uv/getting-started/installation/)
* **gcloud CLI** installed and authenticated. [(Google SDK Installation guide)](https://docs.cloud.google.com/sdk/docs/install-sdk)
* **Application Default Credentials** set with `gcloud auth application-default login`.
* **Google ADK** installed. [(ADK Installation guide)](https://adk.dev/get-started/installation/)
* Optional: **Gemini Enterprise application** with Gemini Enterprise or Business licenses. [(Gemini Enterprise Quickstart Guide)](https://docs.cloud.google.com/gemini/enterprise/docs/quickstart-gemini-enterprise).

### 📊 Billing Export Setup
**Recommended:** Enabling a BigQuery billing export is a highly common and recommended FinOps best practice. The GCP Billing concierge relies on this Billing export for real use.
If you do not have an existing export, the Agent Starter Pack setup includes an option to generate a sample table for testing purposes.

- [Official Documentation: Set up Cloud Billing data export to BigQuery](https://cloud.google.com/billing/docs/how-to/export-data-bigquery)
- **Key Requirement:** You must have the `Billing Account Administrator` role on the Cloud Billing account to enable this export.
- **Latency Note:** Once enabled, it can take 24 to 48 hours for the first data points to appear in BigQuery. 


## 📂 Project Structure

```text
.
├── GCP_billing_concierge /            # Root directory for Agent Logic
│   ├── agent.py                       # Main Orchestrator Agent (Billing Concierge)
│   ├── prompt.py                      # Orchestrator system instructions
│   ├── .env.example                   # Sample .env file for setting env vars
│   ├── tools/
│   │   └── tools.py                   # Custom tools for Logging
│   └── sub_agents/
│       └── finops_infra_agent/        # Specialized agent for Platform Ops
│           ├── agent.py               # Sub-agent: Handles infrastructure tasks
│           ├── prompt.py              # Sub-agent: Instructions for CRON and Monitoring
│           └── tools/
│                └── tools.py          # Custom tools (Scheduler, Alerts, Notifications)
├── deployment_scripts/                
│   ├── deploy_agent.py                # Vertex AI Reasoning Engine deployment script 
│   ├── setup_billing_data.py          # Configures BQ dataset (Real or Mock)
│   └── create_sa.py                   # Provisions the Agent Service Account & IAM
├── mock_data/  
│   ├── billing_export_test_table.json # Sample billing dataset
│   └── billing_schema.json            # Sample billing dataset schema
├── Makefile                       # One-stop shop for Install, Deploy, and Cleanup
└── README.md
```

### Agent Architecture
![Agent Architecture](agent_pattern.png)


### 🔑 Permissions & Roles used

#### For the Admin (You)
* `roles/resourcemanager.projectIamAdmin`: To manage Service Account roles.
* `roles/iam.serviceAccountAdmin`: To create the agent identity.
* `roles/bigquery.admin`: To configure datasets and verify schemas.
* `roles/aiplatform.admin`: To deploy agent to Vertex AI
* `roles/secretmanager.admin`: To create and manage the Agent ID secret.
* `roles/bigquery.dataViewer`: Minimum access needed to the existing **Billing Export table**


#### For the Agent (Service Account)
The `make install` script creates `gcp-billing-concierge-sa` (Service Account used by the agent) and grants the following roles:

**Agent Project (Local Execution):**
- BigQuery: `roles/bigquery.jobUser` (To run analysis jobs).
- AI & Vertex: `roles/aiplatform.user` and `roles/geminidataanalytics.dataAgentStatelessUser`.
- Infrastructure Ops: 
  * `roles/cloudscheduler.admin`: To manage recurring audit schedules.
  * `roles/monitoring.alertPolicyEditor`: To create and edit billing alerts.
  * `roles/monitoring.notificationChannelEditor`: To manage email notification targets.
- Logging: `roles/logging.logWriter` and `roles/logging.configWriter` (For anomaly logging).
- Secrets: `roles/secretmanager.secretAccessor` (To retrieve its own Agent ID).
- Utility: `roles/serviceusage.serviceUsageConsumer` and `roles/telemetry.writer`.
- Identity: `roles/iam.serviceAccountUser `(Granted on the service account itself to allow Scheduler to act as its own identity).

**Billing Project (Data Access)**
Access is granted following the Principle of Least Privilege, targeting only the specific billing export table:

* `roles/bigquery.dataViewer`: Granted at the Table level for the Billing Export data.


## 🚀 Using the agent

There are two primary ways to deploy and use the agent:

1. Fast Track: Deploy directly using the Agent Starter Pack.
2. Custom: Clone the repository for manual customization and deployment.

### Using Agent Starter Pack (Fast Track)

This is the most streamlined option. it allows you to initialize the project structure, provision IAM, and deploy to Vertex AI using a single command chain.

#### Step 1: Set Google Cloud Application Default Credentials
Ensure your local environment is authenticated to your GCP project:
```bash
gcloud auth application-default login
```

#### Step 2: Execute Agent Starter Pack
This command creates a new project directory, installs dependencies, and initiates the backend deployment.
```bash
export AGENT_NAME=billing-concierge-${RANDOM} && \
uvx agent-starter-pack@latest create ${AGENT_NAME} -d agent_engine -ag -a pemujo/GCP-Billing-Concierge && \
cd ${AGENT_NAME} && make install && \
make backend
```

#### Step 3: Post-Deployment & Testing
Once the deployment is complete:
* Test the agent immediately via the Agent Engine Playground in the Google Cloud Console.


* Optionally, to register the agent for use within Gemini Enterprise, run:
```bash
make register-gemini-enterprise
```


### GitHub clone and deploy

This method is better suited for developers who want to customize the agent logic, modify the underlying tools, or integrate with existing infrastructure.

#### Step 1: Enable Required APIs
Set your project ID and enable the necessary Google Cloud services:

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"

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
    secretmanager.googleapis.com \
    --project=$GOOGLE_CLOUD_PROJECT

```
#### Step 2: Environment Configuration

Initialize your environment variables and install the required Python dependencies:

```bash
# Install dependencies
uv sync

# Create your .env file
cp GCP_billing_concierge/.env.example GCP_billing_concierge/.env
```
Update the `.env` file with your specific project and region details. If you already have a BigQuery billing export enabled, enter its details here to skip the sample data step.



```bash
# Vertex AI Agent Engine Configuration
GOOGLE_CLOUD_PROJECT="your-project-id"
GOOGLE_CLOUD_LOCATION="us-central1"

# Billing Data Source (BigQuery)
BILLING_EXPORT_PROJECT_ID="your-billing-project"
BILLING_EXPORT_DATASET="your_dataset"
BILLING_EXPORT_TABLE="your_table"

```

#### Step 3: Provision Billing Data (Optional)
If you do not have a live billing export and wish to test with sample data, run the setup script and follow the prompts to create a mock dataset:


```bash
uv run deployment_scripts/setup_billing_data.py
```


#### Step 4: Provision Identity & Permissions
Run the provisioning script to create the agent's Service Account (`gcp-billing-concierge-sa@<project-id>.iam.gserviceaccount.com.iam.gserviceaccount.com`) and grant the required IAM roles across your project(s):

```bash
uv run deployment_scripts/create_sa.py
```

#### Step 5: Deployment (Still to complete script)
Deploy the agent to Vertex AI Agent Engine:

```bash
uv run deployment_scripts/deploy_agent.py
```

## Appendix: Detailed Agent Starter Pack Flow

If you choose the agent-starter-pack route, here is a brief overview of what happens under the hood:

**Initialization**
The `uvx agent-starter-pack create` command creates a new directory using this repository as a source template. It sets up a standardized project structure for agent_engine deployment, and installs the necessary Python dependencies.


```bash
export AGENT_NAME="billing-concierge"
uvx agent-starter-pack@latest create ${AGENT_NAME} \
    -d agent_engine \
    -ag \
    -a pemujo/GCP-Billing-Concierge
```

**Configuration (make install)**
This will verify the environmental variables are set, it will enable APIs, then execute `setup_billing_data.py` that prompts you for your BILLING_EXPORT_PROJECT_ID and DATASET_ID orif you want to create a sample dataset. It then executes `create_sa.py` to:

* Create a dedicated Service Account.

* Grant the necessary IAM roles (listed above) for both local execution and cross-project billing access.

* Update your .env file with these values.

```bash
make install
```

**Deployment**
Use `make backend` to package the agent and deploy it to **Vertex AI Agent Engine** using Agent Starter Pack deploy script. It takes a few minutes to complete.

```bash
make backend
```


## 📝 Disclaimer
This agent sample is provided for illustrative purposes only and is not intended for production use. It serves as a foundational starting point for teams to develop their own agents.

This sample has not been rigorously tested and does not include production-grade features like robust error handling, advanced security measures, or scalability optimizations. Users are solely responsible for the development, testing, and security hardening of any agents derived from this sample.
