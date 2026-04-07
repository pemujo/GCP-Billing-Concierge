# 💰 FinOps Billing Agent
This agent allows users to query Google Cloud billing and cost data using natural language. It is built with Google ADK, Vertex AI Reasoning Engine (formerly Agent Engine), and BigQuery.

## 📋 Prerequisites
Before running the setup, ensure you have:

* Python 3.10+ and uv installed (pip install uv).
* gcloud CLI installed and configured.
* Application Default Credentials set with gcloud auth application-default login.
* Google ADK installed.
* Agent Starter Pack (Recommended).
* Google Workspace with Gemini Enterprise or Business licenses (Optional).

### 📊 Billing Export Setup
Recommended: Enable a BigQuery billing export to provide the agent with real data. Otherwise, the setup can create a sample table for testing.

- Official Documentation: Set up Cloud Billing data export to BigQuery
- Key Requirement: You must have the Billing Account Administrator role on the Cloud Billing account to enable this export.
- Latency Note: Once enabled, it can take 24 to 48 hours for the first data points to appear in BigQuery. Ensure the export is "Standard" or "Detailed" for SKU-level granularity.


## 🚀 Getting Started

1. Initialize the Project
Use the Agent Starter Pack to scaffold your environment. This command copies the FinOps agent into your local directory.

```bash
export agent_name=gcp_billing_agent
export GOOGLE_CLOUD_PROJECT=your-project-id

export AGENT_NAME=billing-concierge-${RANDOM} && uvx agent-starter-pack=>0.40.0 create ${AGENT_NAME} -d agent_engine -ag -a pemujo/Cloud-AI-FinOps-Agent/ && cd ${AGENT_NAME} && make install && make backend
```

2. Enable Required APIs
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

3. Installation & Deployment
Run the installation command to choose your billing export source and prepare IAM permissions:

``` Bash
make install
```
Deploy the agent to Vertex AI Reasoning Engine:
``` Bash
make deploy
```
Note: This command generates deployment_metadata.json, which is required for automation.

🤖 Automation (Terraform)
Once the agent is deployed, use Terraform to schedule daily audits and set up email alerts for billing anomalies.

Initialize Terraform:

```Bash
cd terraform
terraform init
```

Configure Variables:
Create a my-ai-agent.tfvars file:

```bash
Terraform
project_id  = "your-project-id"
region      = "us-central1"
alert_email = "your-email@company.com"
```

Deploy Automation:

```Bash
terraform apply -var-file="my-ai-agent.tfvars"
```


## 🔑 Permissions & Roles
### For the Admin (Deployment User)
* roles/resourcemanager.projectIamAdmin: To manage Service Account roles.
* roles/iam.serviceAccountAdmin: To create the agent identity.
* roles/bigquery.admin: To configure datasets and verify schemas.

### For the Agent (Service Account)
The make install script creates cloud-ai-finops-agent-sa with the following:

* Execution Project (where the agent runs): 
  - roles/bigquery.jobUser, 
  - roles/aiplatform.user, 
  - roles/serviceusage.serviceUsageConsumer, 
  - roles/geminidataanalytics.dataAgentStatelessUser,
  - roles/telemetry.writer.

* Billing Project: 
  - roles/bigquery.dataViewer.

## 📝 Disclaimer
This agent sample is provided for illustrative purposes only and is not intended for production use. It serves as a foundational starting point for teams to develop their own agents.

This sample has not been rigorously tested and does not include production-grade features like robust error handling, advanced security measures, or scalability optimizations. Users are solely responsible for the development, testing, and security hardening of any agents derived from this sample.