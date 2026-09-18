# --- Load Environment ---
export PATH := $(HOME)/.local/bin:$(PATH)
ENV_FILE := .env
-include $(ENV_FILE)
-include GCP_billing_concierge/.env

GOOGLE_CLOUD_REGION ?= us-central1
GOOGLE_CLOUD_LOCATION ?= $(GOOGLE_CLOUD_REGION)
G_SUGGESTION := $(shell gcloud config get-value project 2>/dev/null)
AGENT_ID_SECRET_NAME ?= billing-concierge-agent-id
METADATA_FILE ?= deployment_metadata.json

.PHONY: install playground run deploy eval store_agent_id

# --- 1. Provision Cloud Infrastructure & Service Account ---
install:
	@$(MAKE) check-env
	@$(MAKE) enable_apis
	@$(MAKE) setup_billing_data
	@$(MAKE) create_sa
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
		LOC=$$(grep "^GOOGLE_CLOUD_LOCATION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2); \
		REGION_VAL=$${LOC:-us-central1}; \
		echo "GOOGLE_CLOUD_REGION=$$REGION_VAL" >> $(ENV_FILE); \
		echo "✅ Set Cloud Region: $$REGION_VAL in $(ENV_FILE)"; \
	else \
		echo "✅ Cloud Region: $$(grep "^GOOGLE_CLOUD_REGION=" $(ENV_FILE) | cut -d'=' -f2)"; \
	fi
	@if [ -z "$$(grep "^GOOGLE_CLOUD_LOCATION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2)" ]; then \
		REGION_VAL=$$(grep "^GOOGLE_CLOUD_REGION=" $(ENV_FILE) | cut -d'=' -f2); \
		echo "GOOGLE_CLOUD_LOCATION=$${REGION_VAL:-us-central1}" >> $(ENV_FILE); \
		echo "✅ Set Gemini Model Location: $${REGION_VAL:-us-central1} in $(ENV_FILE)"; \
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

create_sa:
	@uv run python deployment_scripts/create_sa.py

# --- 2. Local Interactive Agent Playground ---
playground:
	@echo "🌐 Starting local Agent Playground..."
	@uv run agents-cli playground

run: playground

# --- 3. Deploy to Agent Runtime & Sync Secret Manager ---
deploy:
	@echo "🚀 Deploying GCP Billing Concierge to Agent Runtime..."
	@if [ -f GCP_billing_concierge/.env ] && [ ! -f .env ]; then cp -f GCP_billing_concierge/.env .env; fi
	@cp -f .env GCP_billing_concierge/.env 2>/dev/null || true
	$(eval DEPLOY_REGION := $(shell grep "^GOOGLE_CLOUD_REGION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2))
	$(eval DEPLOY_REGION := $(if $(DEPLOY_REGION),$(DEPLOY_REGION),us-central1))
	$(eval AGENT_SA := $(shell grep "^AGENT_SERVICE_ACCOUNT=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2))
	$(eval AGENT_SA := $(if $(AGENT_SA),$(AGENT_SA),gcp-billing-concierge-sa@$(GOOGLE_CLOUD_PROJECT).iam.gserviceaccount.com))
	$(eval BQ_PROJECT := $(shell grep "^BILLING_EXPORT_PROJECT_ID=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval BQ_DATASET := $(shell grep "^BILLING_EXPORT_DATASET=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval BQ_TABLE := $(shell grep "^BILLING_EXPORT_TABLE=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval BQ_LOC := $(shell grep "^BIGQUERY_LOCATION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval BQ_LOC := $(if $(BQ_LOC),$(BQ_LOC),US))
	$(eval AUTH_KEY := $(shell grep "^AUTH_ID=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2 | tr -d ' "'))
	$(eval AUTH_KEY := $(if $(AUTH_KEY),$(AUTH_KEY),bq-agent))
	@echo "🚀 Deploying with Service Account: $(AGENT_SA)..."
	@uv run agents-cli deploy \
		--project="$(GOOGLE_CLOUD_PROJECT)" \
		--region="$(DEPLOY_REGION)" \
		--update-env-vars="GOOGLE_CLOUD_REGION=$(DEPLOY_REGION),AUTH_ID=$(AUTH_KEY),ENABLE_USER_OAUTH=true,REQUIRE_USER_OAUTH=true,BILLING_EXPORT_PROJECT_ID=$(BQ_PROJECT),BILLING_EXPORT_DATASET=$(BQ_DATASET),BILLING_EXPORT_TABLE=$(BQ_TABLE),BIGQUERY_LOCATION=$(BQ_LOC)" \
		--service-account="$(AGENT_SA)"
	@$(MAKE) store_agent_id

# --- Store Agent ID in Secret Manager for Automation / Cloud Scheduler ---
store_agent_id:
	@echo "🔐 Extracting Agent ID and storing in Secret Manager..."
	@G_PROJECT=$$(grep "^GOOGLE_CLOUD_PROJECT=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2); \
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
	if ! gcloud secrets describe $(AGENT_ID_SECRET_NAME) --project=$$G_PROJECT > /dev/null 2>&1; then \
		echo "🆕 Creating secret $(AGENT_ID_SECRET_NAME)..."; \
		gcloud secrets create $(AGENT_ID_SECRET_NAME) --replication-policy="automatic" --project=$$G_PROJECT; \
	fi; \
	printf "%s" "$$AGENT_ID" | gcloud secrets versions add $(AGENT_ID_SECRET_NAME) --data-file=- --project=$$G_PROJECT; \
	echo "✅ Agent ID successfully stored in secret: $(AGENT_ID_SECRET_NAME) ($$AGENT_ID)"

# --- 4. Benchmark Accuracy against Golden Dataset ---
eval:
	@echo "🧪 Running evaluations with agents-cli..."
	@uv run agents-cli eval run \
		--dataset new_agent_evals/billing_eval_dataset_fully_modern.evalset.json \
		--config new_agent_evals/eval_config.json \
		--app-name GCP_billing_concierge \
		--output new_agent_evals/results
