# --- Load Environment ---
ENV_FILE := .env
-include $(ENV_FILE)
-include GCP_billing_concierge/.env

GOOGLE_CLOUD_LOCATION ?= us-central1
G_SUGGESTION := $(shell gcloud config get-value project 2>/dev/null)
AGENT_ID_SECRET_NAME ?= billing-concierge-agent-id
METADATA_FILE ?= deployment_metadata.json

.PHONY: install playground run deploy eval

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
	@if [ -z "$$(grep "^GOOGLE_CLOUD_LOCATION=" $(ENV_FILE) 2>/dev/null | cut -d'=' -f2)" ]; then \
		echo "GOOGLE_CLOUD_LOCATION=us-central1" >> $(ENV_FILE); \
		echo "✅ Set default Region: us-central1 in $(ENV_FILE)"; \
	else \
		echo "✅ Region:  $$(grep "^GOOGLE_CLOUD_LOCATION=" $(ENV_FILE) | cut -d'=' -f2)"; \
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
	@uv run agents-cli deploy --project="$(GOOGLE_CLOUD_PROJECT)" --region="$(GOOGLE_CLOUD_LOCATION)" $(if $(AGENT_SERVICE_ACCOUNT),--service-account="$(AGENT_SERVICE_ACCOUNT)")
	@python3 -c "import json, os, subprocess; \
	meta = json.load(open('$(METADATA_FILE)')) if os.path.exists('$(METADATA_FILE)') else {}; \
	aid = meta.get('remote_agent_runtime_id') or meta.get('remote_agent_engine_id') or meta.get('resource_name') or meta.get('agent_id', ''); \
	subprocess.run(['gcloud', 'secrets', 'describe', '$(AGENT_ID_SECRET_NAME)', '--project=$(GOOGLE_CLOUD_PROJECT)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0 and subprocess.run(['gcloud', 'secrets', 'create', '$(AGENT_ID_SECRET_NAME)', '--replication-policy=automatic', '--project=$(GOOGLE_CLOUD_PROJECT)']); \
	aid and subprocess.run(['gcloud', 'secrets', 'versions', 'add', '$(AGENT_ID_SECRET_NAME)', '--data-file=-', '--project=$(GOOGLE_CLOUD_PROJECT)'], input=aid.encode()); \
	print(f'✅ Synced Agent ID ({aid}) to Secret Manager: $(AGENT_ID_SECRET_NAME)') if aid else None" 2>/dev/null || true

# --- 4. Benchmark Accuracy against Golden Dataset ---
eval:
	@echo "🧪 Running evaluations with agents-cli..."
	@uv run agents-cli eval run \
		--dataset new_agent_evals/billing_eval_dataset_fully_modern.evalset.json \
		--config new_agent_evals/eval_config.json \
		--app-name GCP_billing_concierge \
		--output new_agent_evals/results
