# --- Load env file ---
ENV_FILE := GCP_billing_concierge/.env
-include $(ENV_FILE)

# --- Default Fallback Region ---
GOOGLE_CLOUD_LOCATION ?= us-central1

# --- Get gcloud active Project ID ---
G_SUGGESTION := $(shell gcloud config get-value project 2>/dev/null)

# --- Variables ---
AGENT_ID_SECRET_NAME = billing-concierge-agent-id
METADATA_FILE = deployment_metadata.json

.PHONY: install check-env enable_apis setup_billing_data create_sa run deploy eval store_agent_id clean

install:
	@$(MAKE) check-env
	@$(MAKE) enable_apis
	@$(MAKE) setup_billing_data
	@$(MAKE) create_sa

check-env:
	@if [ -z "$(GOOGLE_CLOUD_PROJECT)" ]; then \
		read -p "GOOGLE_CLOUD_PROJECT variable not set. Use gcloud active project ID: [$(G_SUGGESTION)]? (Hit Enter for yes, or type the Project ID): " input; \
		FINAL_ID=$${input:-$(G_SUGGESTION)}; \
		if [ -z "$$FINAL_ID" ]; then echo "❌ Error: Project ID required."; exit 1; fi; \
		echo "GOOGLE_CLOUD_PROJECT=$$FINAL_ID" >> $(ENV_FILE); \
		echo "✅ Saved Project: $$FINAL_ID to .env"; \
	else \
		echo "✅ Project: $(GOOGLE_CLOUD_PROJECT)"; \
	fi
	@if [ -z "$(GOOGLE_CLOUD_LOCATION)" ]; then \
		echo "GOOGLE_CLOUD_LOCATION=us-central1" >> $(ENV_FILE); \
		echo "✅ Set default Region: us-central1 in .env"; \
	else \
		echo "✅ Region:  $(GOOGLE_CLOUD_LOCATION)"; \
	fi
	@if ! grep -q "^GOOGLE_GENAI_USE_VERTEXAI=" $(ENV_FILE) 2>/dev/null; then \
		echo "GOOGLE_GENAI_USE_VERTEXAI=true" >> $(ENV_FILE); \
		echo "✅ Enabled Vertex AI backend (GOOGLE_GENAI_USE_VERTEXAI=true) in .env"; \
	fi

enable_apis:
	@echo "Enabling Google Cloud APIs..."
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

# --- Local Run / Chat with Agent ---
run:
	@echo "💬 Starting local interactive session with GCP Billing Concierge..."
	@uvx google-agents-cli run || uv run agents-cli run

# --- Deployment using agents-cli to Vertex AI Agent Runtime ---
deploy:
	$(eval AGENT_SA := $(shell grep "^AGENT_SERVICE_ACCOUNT=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2))
	$(eval G_PROJECT := $(shell grep "^GOOGLE_CLOUD_PROJECT=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2))
	$(eval G_LOCATION := $(shell grep "^GOOGLE_CLOUD_LOCATION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2))
	@echo "🚀 Deploying GCP Billing Concierge to Vertex AI Agent Runtime..."
	@if [ -n "$(AGENT_SA)" ]; then \
		uvx google-agents-cli deploy --project="$(G_PROJECT)" --region="$(G_LOCATION)" --service-account="$(AGENT_SA)" || \
		uv run agents-cli deploy --project="$(G_PROJECT)" --region="$(G_LOCATION)" --service-account="$(AGENT_SA)"; \
	else \
		uvx google-agents-cli deploy --project="$(G_PROJECT)" --region="$(G_LOCATION)" || \
		uv run agents-cli deploy --project="$(G_PROJECT)" --region="$(G_LOCATION)"; \
	fi
	@$(MAKE) store_agent_id

# --- Store Agent ID in Secret Manager for Cloud Scheduler Integration ---
store_agent_id:
	@echo "🔐 Extracting Agent ID and storing in Secret Manager..."
	@G_PROJECT=$$(grep "^GOOGLE_CLOUD_PROJECT=" $(ENV_FILE) | cut -d'=' -f2); \
	AGENT_ID=$$(python3 -c "import json; data=json.load(open('$(METADATA_FILE)')); print(data.get('remote_agent_engine_id') or data.get('resource_name') or data.get('agent_id', ''))" 2>/dev/null); \
	if [ -z "$$AGENT_ID" ]; then \
		echo "⚠️ Note: Could not extract agent ID from $(METADATA_FILE). Verify deployment output."; \
	else \
		if ! gcloud secrets describe $(AGENT_ID_SECRET_NAME) --project=$$G_PROJECT > /dev/null 2>&1; then \
			echo "🆕 Creating secret $(AGENT_ID_SECRET_NAME)..."; \
			gcloud secrets create $(AGENT_ID_SECRET_NAME) --replication-policy="automatic" --project=$$G_PROJECT; \
		fi; \
		printf "%s" "$$AGENT_ID" | gcloud secrets versions add $(AGENT_ID_SECRET_NAME) --data-file=- --project=$$G_PROJECT; \
		echo "✅ Agent ID successfully stored in secret: $(AGENT_ID_SECRET_NAME) ($$AGENT_ID)"; \
	fi

# --- Run Evals ---
eval:
	@echo "🧪 Running evaluations..."
	@cd gcp_billing_concierge_agent_evals && uv run python run_eval.py

