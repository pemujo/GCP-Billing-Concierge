# --- Load Environment ---
export PATH := $(HOME)/.local/bin:$(PATH)
ENV_FILE := .env
-include $(ENV_FILE)
-include GCP_billing_concierge/.env

GOOGLE_CLOUD_REGION ?= us-central1
GOOGLE_CLOUD_LOCATION ?= global
G_SUGGESTION := $(shell gcloud config get-value project 2>/dev/null)
METADATA_FILE ?= deployment_metadata.json

# Resolve dynamic agent name and Secret Manager secret name
RESOLVED_AGENT_NAME = $(if $(AGENT_NAME),$(AGENT_NAME),$(if $(shell grep "^AGENT_NAME=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'),$(shell grep "^AGENT_NAME=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'),GCP_billing_concierge))
CLEAN_AGENT_NAME = $(shell echo "$(RESOLVED_AGENT_NAME)" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd 'a-z0-9-')
DEFAULT_SECRET_NAME = $(if $(filter GCP_billing_concierge gcp-billing-concierge,$(RESOLVED_AGENT_NAME)),billing-concierge-agent-id,$(CLEAN_AGENT_NAME)-agent-id)
AGENT_ID_SECRET_NAME ?= $(if $(shell grep "^AGENT_ID_SECRET_NAME=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'),$(shell grep "^AGENT_ID_SECRET_NAME=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'),$(DEFAULT_SECRET_NAME))
DEFAULT_SA_ID = $(if $(filter GCP_billing_concierge gcp-billing-concierge,$(RESOLVED_AGENT_NAME)),gcp-billing-concierge-sa,$(shell echo "$(CLEAN_AGENT_NAME)-sa" | cut -c 1-30))
DEFAULT_AUTH_ID = $(if $(filter GCP_billing_concierge gcp-billing-concierge,$(RESOLVED_AGENT_NAME)),billing-ge-oauth,$(CLEAN_AGENT_NAME)-oauth)
SANITIZED_APP_NAME = $(shell python3 -c "import re; raw = '$(RESOLVED_AGENT_NAME)'; name = re.sub(r'[^a-zA-Z0-9_]', '_', raw); print(f'agent_{name}' if not name or name[0].isdigit() else name)" 2>/dev/null || echo "GCP_billing_concierge")
EVAL_APP_NAME ?= $(if $(APP_NAME),$(APP_NAME),$(SANITIZED_APP_NAME))
DEFAULT_AGENT_DESC := FinOps billing concierge for Google Cloud cost analysis, anomaly detection, and automated spend monitoring.
AGENT_DESCRIPTION ?= $(if $(shell grep "^AGENT_DESCRIPTION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d '"'),$(shell grep "^AGENT_DESCRIPTION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d '"'),$(DEFAULT_AGENT_DESC))

.PHONY: install playground run deploy eval store_agent_id configure-gemini-oauth publish select_identity configure_identity grant_agent_identity_iam

# --- 1. Provision Cloud Infrastructure & Identity ---
install:
	@$(MAKE) check-env
	@$(MAKE) enable_apis
	@$(MAKE) setup_billing_data
	@$(MAKE) select_identity
	@$(MAKE) configure_identity
	@cp -f GCP_billing_concierge/.env .env 2>/dev/null || cp -f .env GCP_billing_concierge/.env 2>/dev/null || true

check-env:
	@touch $(ENV_FILE)
	@if [ -f GCP_billing_concierge/.env ] && [ ! -s $(ENV_FILE) ]; then cp -f GCP_billing_concierge/.env $(ENV_FILE); fi
	@if [ -z "$$(grep "^GOOGLE_CLOUD_PROJECT=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2)" ]; then \
		read -p "GOOGLE_CLOUD_PROJECT variable not set. Use gcloud active project ID: [$(G_SUGGESTION)]? (Hit Enter for yes, or type the Project ID): " input; \
		FINAL_ID=$${input:-$(G_SUGGESTION)}; \
		if [ -z "$$FINAL_ID" ]; then echo "❌ Error: Project ID required."; exit 1; fi; \
		echo "GOOGLE_CLOUD_PROJECT=$$FINAL_ID" >> $(ENV_FILE); \
		echo "✅ Saved Project: $$FINAL_ID to $(ENV_FILE)"; \
	else \
		echo "✅ Project: $$(grep "^GOOGLE_CLOUD_PROJECT=" $(ENV_FILE) | cut -d'=' -f2)"; \
	fi
	@if [ -z "$$(grep "^GOOGLE_CLOUD_REGION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2)" ]; then \
		echo "GOOGLE_CLOUD_REGION=us-central1" >> $(ENV_FILE); \
		echo "✅ Set Cloud Region (Agent Engine): us-central1 in $(ENV_FILE)"; \
	else \
		echo "✅ Cloud Region (Agent Engine): $$(grep "^GOOGLE_CLOUD_REGION=" $(ENV_FILE) | cut -d'=' -f2)"; \
	fi
	@if [ -z "$$(grep "^GOOGLE_CLOUD_LOCATION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2)" ]; then \
		echo "GOOGLE_CLOUD_LOCATION=global" >> $(ENV_FILE); \
		echo "✅ Set Gemini Model Location: global in $(ENV_FILE)"; \
	else \
		echo "✅ Gemini Model Location: $$(grep "^GOOGLE_CLOUD_LOCATION=" $(ENV_FILE) | cut -d'=' -f2)"; \
	fi
	@if ! grep -q "^GOOGLE_GENAI_USE_VERTEXAI=" $(ENV_FILE) 2>/dev/null; then \
		echo "GOOGLE_GENAI_USE_VERTEXAI=true" >> $(ENV_FILE); \
		echo "✅ Enabled Cloud backend (GOOGLE_GENAI_USE_VERTEXAI=true) in $(ENV_FILE)"; \
	fi
	@cp -f $(ENV_FILE) GCP_billing_concierge/.env 2>/dev/null || true

enable_apis:
	@echo "🔧 Enabling Google Cloud APIs for $(GOOGLE_CLOUD_PROJECT)..."
	@gcloud services enable \
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
		--project=$(GOOGLE_CLOUD_PROJECT)

setup_billing_data:
	@uv run python deployment_scripts/setup_billing_data.py

select_identity:
	@uv run python deployment_scripts/select_identity.py 2>/dev/null || python3 deployment_scripts/select_identity.py

configure_identity:
	$(eval ID_TYPE := $(shell grep "^IDENTITY_TYPE=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval ID_TYPE := $(if $(ID_TYPE),$(ID_TYPE),agent_identity))
	@if [ "$(ID_TYPE)" = "service_account" ]; then \
		$(MAKE) create_sa; \
	else \
		echo ""; \
		echo "ℹ️ Agent Identity selected (Keyless Zero-Trust)."; \
		echo "   Service Account creation skipped."; \
		echo "   Application IAM roles will be automatically granted to the agent's keyless principal post-deployment during 'make deploy'."; \
		echo ""; \
	fi

create_sa:
	@uv run python deployment_scripts/create_sa.py --agent-name="$(RESOLVED_AGENT_NAME)" 2>/dev/null || python3 deployment_scripts/create_sa.py --agent-name="$(RESOLVED_AGENT_NAME)"

grant_agent_identity_iam:
	@echo "🔐 Configuring Post-Deployment IAM Roles for Agent Identity..."
	@uv run python deployment_scripts/grant_agent_identity_iam.py --agent-name="$(RESOLVED_AGENT_NAME)" --secret-name="$(AGENT_ID_SECRET_NAME)" 2>/dev/null || python3 deployment_scripts/grant_agent_identity_iam.py --agent-name="$(RESOLVED_AGENT_NAME)" --secret-name="$(AGENT_ID_SECRET_NAME)"

configure-gemini-oauth:
	@bash deployment_scripts/configure_gemini_enterprise_oauth.sh "$(RESOLVED_AGENT_NAME)" "$(AUTH_ID)"

publish:
	@echo "📢 Publishing agent to Gemini Enterprise..."
	@uvx google-agents-cli publish gemini-enterprise --interactive --description="$(AGENT_DESCRIPTION)" --tool-description="$(AGENT_DESCRIPTION)"

# --- 2. Local Interactive Agent Playground ---
playground:
	@echo "🌐 Starting local Agent Playground..."
	@uv run agents-cli playground

run: playground

# --- 3. Deploy to Agent Runtime & Sync Secret Manager ---
deploy:
	$(eval DEPLOY_NAME := $(RESOLVED_AGENT_NAME))
	$(eval SECRET_NAME := $(AGENT_ID_SECRET_NAME))
	@echo "🚀 Deploying '$(DEPLOY_NAME)' to Agent Runtime..."
	@if [ -f GCP_billing_concierge/.env ] && [ ! -f .env ]; then cp -f GCP_billing_concierge/.env .env; fi
	@cp -f .env GCP_billing_concierge/.env 2>/dev/null || true
	$(eval DEPLOY_REGION := $(shell grep "^GOOGLE_CLOUD_REGION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval DEPLOY_REGION := $(if $(DEPLOY_REGION),$(DEPLOY_REGION),us-central1))
	$(eval DEPLOY_LOCATION := $(shell grep "^GOOGLE_CLOUD_LOCATION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval DEPLOY_LOCATION := $(if $(DEPLOY_LOCATION),$(DEPLOY_LOCATION),global))
	$(eval DEFAULT_SA := $(DEFAULT_SA_ID)@$(GOOGLE_CLOUD_PROJECT).iam.gserviceaccount.com)
	$(eval AGENT_SA := $(shell grep "^AGENT_SERVICE_ACCOUNT=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2))
	$(eval AGENT_SA := $(if $(AGENT_SA),$(AGENT_SA),$(DEFAULT_SA)))
	$(eval BQ_PROJECT := $(shell grep "^BILLING_EXPORT_PROJECT_ID=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval BQ_DATASET := $(shell grep "^BILLING_EXPORT_DATASET=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval BQ_TABLE := $(shell grep "^BILLING_EXPORT_TABLE=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval BQ_LOC := $(shell grep "^BIGQUERY_LOCATION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval BQ_LOC := $(if $(BQ_LOC),$(BQ_LOC),US))
	$(eval ENV_AUTH := $(shell grep "^AUTH_ID=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval AUTH_KEY := $(if $(AUTH_ID),$(AUTH_ID),$(if $(filter billing-ge-oauth bq-agent,$(ENV_AUTH)),$(if $(filter GCP_billing_concierge gcp-billing-concierge,$(RESOLVED_AGENT_NAME)),$(ENV_AUTH),$(DEFAULT_AUTH_ID)),$(if $(ENV_AUTH),$(ENV_AUTH),$(DEFAULT_AUTH_ID)))))
	$(eval USER_OAUTH := $(shell grep "^ENABLE_USER_OAUTH=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval USER_OAUTH := $(if $(USER_OAUTH),$(USER_OAUTH),true))
	$(eval REQ_OAUTH := $(shell grep "^REQUIRE_USER_OAUTH=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval REQ_OAUTH := $(if $(REQ_OAUTH),$(REQ_OAUTH),$(USER_OAUTH)))
	$(eval DEPLOY_NAME := $(RESOLVED_AGENT_NAME))
	$(eval SECRET_NAME := $(AGENT_ID_SECRET_NAME))
	$(eval ID_TYPE := $(if $(IDENTITY_TYPE),$(IDENTITY_TYPE),$(shell grep "^IDENTITY_TYPE=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "')))
	$(eval ID_TYPE := $(if $(ID_TYPE),$(ID_TYPE),agent_identity))
	@if [ "$(ID_TYPE)" = "agent_identity" ]; then \
		echo "🚀 Deploying agent '$(DEPLOY_NAME)' with Native Agent Identity (Keyless)..."; \
		uv run agents-cli deploy \
			--project="$(GOOGLE_CLOUD_PROJECT)" \
			--region="$(DEPLOY_REGION)" \
			--service-name="$(DEPLOY_NAME)" \
			--agent-identity \
			--update-env-vars="GOOGLE_CLOUD_REGION=$(DEPLOY_REGION),GOOGLE_CLOUD_LOCATION=$(DEPLOY_LOCATION),AGENT_NAME=$(DEPLOY_NAME),AGENT_ID_SECRET_NAME=$(SECRET_NAME),AUTH_ID=$(AUTH_KEY),ENABLE_USER_OAUTH=$(USER_OAUTH),REQUIRE_USER_OAUTH=$(REQ_OAUTH),BILLING_EXPORT_PROJECT_ID=$(BQ_PROJECT),BILLING_EXPORT_DATASET=$(BQ_DATASET),BILLING_EXPORT_TABLE=$(BQ_TABLE),BIGQUERY_LOCATION=$(BQ_LOC)"; \
		$(MAKE) grant_agent_identity_iam AGENT_NAME="$(DEPLOY_NAME)" AGENT_ID_SECRET_NAME="$(SECRET_NAME)"; \
	else \
		echo "🚀 Deploying agent '$(DEPLOY_NAME)' with Service Account: $(AGENT_SA)..."; \
		uv run agents-cli deploy \
			--project="$(GOOGLE_CLOUD_PROJECT)" \
			--region="$(DEPLOY_REGION)" \
			--service-name="$(DEPLOY_NAME)" \
			--service-account="$(AGENT_SA)" \
			--update-env-vars="GOOGLE_CLOUD_REGION=$(DEPLOY_REGION),GOOGLE_CLOUD_LOCATION=$(DEPLOY_LOCATION),AGENT_NAME=$(DEPLOY_NAME),AGENT_ID_SECRET_NAME=$(SECRET_NAME),AUTH_ID=$(AUTH_KEY),ENABLE_USER_OAUTH=$(USER_OAUTH),REQUIRE_USER_OAUTH=$(REQ_OAUTH),BILLING_EXPORT_PROJECT_ID=$(BQ_PROJECT),BILLING_EXPORT_DATASET=$(BQ_DATASET),BILLING_EXPORT_TABLE=$(BQ_TABLE),BIGQUERY_LOCATION=$(BQ_LOC)"; \
	fi
	@$(MAKE) store_agent_id AGENT_NAME="$(DEPLOY_NAME)" AGENT_ID_SECRET_NAME="$(SECRET_NAME)"

# --- Store Agent ID in Secret Manager for Automation / Cloud Scheduler ---
store_agent_id:
	@echo "🔐 Extracting Agent ID and storing in Secret Manager..."
	@G_PROJECT=$$(grep "^GOOGLE_CLOUD_PROJECT=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'); \
	python3 -c "import json, os; \
	if os.path.exists('$(METADATA_FILE)'): \
		with open('$(METADATA_FILE)', 'r+') as f: \
			data = json.load(f); \
			aid = data.get('remote_agent_runtime_id') or data.get('remote_agent_engine_id') or data.get('resource_name') or data.get('agent_id', ''); \
			if aid and 'remote_agent_engine_id' not in data: \
				data['remote_agent_engine_id'] = aid; \
				f.seek(0); json.dump(data, f, indent=2); f.truncate()" 2>/dev/null || true; \
	AGENT_ID=$$(python3 -c "import json, os; \
	meta = json.load(open('$(METADATA_FILE)')) if os.path.exists('$(METADATA_FILE)') else {}; \
	print(meta.get('remote_agent_runtime_id') or meta.get('remote_agent_engine_id') or meta.get('resource_name') or meta.get('agent_id', ''))" 2>/dev/null); \
	if [ -z "$$AGENT_ID" ]; then \
		echo "❌ Error: Could not extract agent ID from $(METADATA_FILE). Verify deployment output."; \
		exit 1; \
	fi; \
	if [ -z "$$G_PROJECT" ]; then \
		echo "❌ Error: Could not find GOOGLE_CLOUD_PROJECT in $(ENV_FILE)"; \
		exit 1; \
	fi; \
	TARGET_SEC="$(AGENT_ID_SECRET_NAME)"; \
	if [ -z "$$TARGET_SEC" ]; then TARGET_SEC="$(DEFAULT_SECRET_NAME)"; fi; \
	if ! gcloud secrets describe $$TARGET_SEC --project=$$G_PROJECT > /dev/null 2>&1; then \
		echo "🆕 Creating secret $$TARGET_SEC..."; \
		gcloud secrets create $$TARGET_SEC --replication-policy="automatic" --project=$$G_PROJECT; \
	fi; \
	printf "%s" "$$AGENT_ID" | gcloud secrets versions add $$TARGET_SEC --data-file=- --project=$$G_PROJECT; \
	echo "✅ Agent ID successfully stored in secret: $$TARGET_SEC ($$AGENT_ID)"

# --- 4. Benchmark Accuracy against Golden Dataset ---
eval:
	@echo "🧪 Running evaluations for app '$(EVAL_APP_NAME)' with agents-cli..."
	@uv run agents-cli eval run \
		--dataset new_agent_evals/billing_eval_dataset_fully_modern.evalset.json \
		--config new_agent_evals/eval_config.json \
		--app-name $(EVAL_APP_NAME) \
		--output new_agent_evals/results
