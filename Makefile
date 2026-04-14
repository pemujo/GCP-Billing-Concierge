.PHONY: setup_billing_data  create_sa deploy

-include Cloud_AI_FinOps_Agent/.env

install: enable_apis setup_billing_data  create_sa

GOOGLE_CLOUD_PROJECT ?= $(shell gcloud config get-value project 2>/dev/null)

check-env:
ifeq ($(strip $(GOOGLE_CLOUD_PROJECT)),)
	$(error "GOOGLE_CLOUD_PROJECT is not set in Env, .env file, or gcloud config. Please set it and try again.")
endif
	@echo "✅ Using Project: $(GOOGLE_CLOUD_PROJECT)"

enable_apis: check-env
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
	@uv run python scripts/setup_billing_data.py

create_sa:
	@uv run python scripts/create_sa.py

deploy:
	# Extract the SA from .env at runtime
	$(eval AGENT_SA := $(shell grep "^AGENT_SERVICE_ACCOUNT=" Cloud_AI_FinOps_Agent/.env | cut -d'=' -f2))
	$(eval G_PROJECT := $(shell grep "^GOOGLE_CLOUD_PROJECT=" Cloud_AI_FinOps_Agent/.env | cut -d'=' -f2))
	@echo "🚀 Deploying as $(AGENT_SA)..."
	(uv export --no-hashes --no-header --no-dev --no-emit-project --no-annotate > Cloud_AI_FinOps_Agent/app_utils/.requirements.txt 2>/dev/null || \
	uv export --no-hashes --no-header --no-dev --no-emit-project > Cloud_AI_FinOps_Agent/app_utils/.requirements.txt) && \
	uv run -m Cloud_AI_FinOps_Agent.app_utils.deploy \
		--project="$(G_PROJECT)" \
		--source-packages=./Cloud_AI_FinOps_Agent \
		--entrypoint-module=Cloud_AI_FinOps_Agent.agent_engine_app \
		--entrypoint-object=agent_engine \
		--requirements-file=Cloud_AI_FinOps_Agent/app_utils/.requirements.txt \
		--service-account="$(AGENT_SA)" \
		$(if $(AGENT_IDENTITY),--agent-identity) \
		$(if $(filter command line,$(origin SECRETS)),--set-secrets="$(SECRETS)")
	# Trigger the secret storage 
	@$(MAKE) store_agent_id

# --- Variables ---
AGENT_ID_SECRET_NAME = finops-agent-id
METADATA_FILE = deployment_metadata.json

# --- New Target: Store Agent ID in Secret Manager ---
store_agent_id:
	@echo "🔐 Extracting Agent ID and storing in Secret Manager..."
	@G_PROJECT=$$(grep "^GOOGLE_CLOUD_PROJECT=" Cloud_AI_FinOps_Agent/.env | cut -d'=' -f2); \
	AGENT_ID=$$(python3 -c "import json; print(json.load(open('$(METADATA_FILE)'))['remote_agent_engine_id'])" 2>/dev/null); \
	if [ -z "$$AGENT_ID" ]; then \
		echo "❌ Error: Could not extract agent ID from $(METADATA_FILE)."; \
		exit 1; \
	fi; \
	if [ -z "$$G_PROJECT" ]; then \
		echo "❌ Error: Could not find GOOGLE_CLOUD_PROJECT in .env"; \
		exit 1; \
	fi; \
	if ! gcloud secrets describe $(AGENT_ID_SECRET_NAME) --project=$$G_PROJECT > /dev/null 2>&1; then \
		echo "🆕 Creating secret $(AGENT_ID_SECRET_NAME)..."; \
		gcloud secrets create $(AGENT_ID_SECRET_NAME) --replication-policy="automatic" --project=$$G_PROJECT; \
	fi; \
	printf "%s" "$$AGENT_ID" | gcloud secrets versions add $(AGENT_ID_SECRET_NAME) --data-file=- --project=$$G_PROJECT; \
	echo "\n✅ Agent ID successfully stored in secret: $(AGENT_ID_SECRET_NAME)"

