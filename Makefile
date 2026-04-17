# --- Load env file ---
ENV_FILE := GCP_billing_concierge/.env
-include $(ENV_FILE)

# ---  Verified Location ---
ifeq ($(strip $(GOOGLE_CLOUD_LOCATION)),)
    # We add the current directory to PYTHONPATH so the import works
	ASP_LOCATION := $(shell PYTHONPATH=. python3 -c "import click; from GCP_billing_concierge.app_utils.deploy import deploy_agent_engine_app; print(next(o.default for o in deploy_agent_engine_app.params if o.name == 'location'))" 2>/dev/null)
    ifneq ($(strip $(ASP_LOCATION)),)
        GOOGLE_CLOUD_LOCATION := $(ASP_LOCATION)
        _sync := $(shell echo "GOOGLE_CLOUD_LOCATION=$(GOOGLE_CLOUD_LOCATION)" >> $(ENV_FILE))
        SOURCE_MSG := "Discovered from ASP run and saved to .env"
    else
        GOOGLE_CLOUD_LOCATION := UNKNOWN
        SOURCE_MSG := "NOT FOUND"
    endif
else
    SOURCE_MSG := "Loaded from .env"
endif

# --- Get gcloud active Project ID ---
G_SUGGESTION := $(shell gcloud config get-value project 2>/dev/null)

.PHONY: install check-env enable_apis setup_billing_data create_sa

install:
	@$(MAKE) check-env
	@$(MAKE) enable_apis
	@$(MAKE) setup_billing_data
	@$(MAKE) create_sa


check-env:
	@# Interactive Project Check
	@if [ -z "$(GOOGLE_CLOUD_PROJECT)" ]; then \
		read -p "GOOGLE_CLOUD_PROJECT variable not set. Use gcloud active project ID: [$(G_SUGGESTION)]? (Hit Enter for yes, or type the Project ID): " input; \
		FINAL_ID=$${input:-$(G_SUGGESTION)}; \
		if [ -z "$$FINAL_ID" ]; then echo "❌ Error: Project ID required."; exit 1; fi; \
		echo "GOOGLE_CLOUD_PROJECT=$$FINAL_ID" >> $(ENV_FILE); \
		echo "✅ Saved Project: $$FINAL_ID to .env"; \
	else \
		echo "✅ Project: $(GOOGLE_CLOUD_PROJECT)"; \
	fi
	@if [ "$(GOOGLE_CLOUD_LOCATION)" = "UNKNOWN" ]; then echo "❌ Location not set"; exit 1; fi
	@echo "✅ Region:  $(GOOGLE_CLOUD_LOCATION)"


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

deploy:
	# Extract the SA from .env at runtime
	$(eval AGENT_SA := $(shell grep "^AGENT_SERVICE_ACCOUNT=" GCP_billing_concierge/.env | cut -d'=' -f2))
	$(eval G_PROJECT := $(shell grep "^GOOGLE_CLOUD_PROJECT=" GCP_billing_concierge/.env | cut -d'=' -f2))
	@echo "🚀 Deploying as $(AGENT_SA)..."
	(uv export --no-hashes --no-header --no-dev --no-emit-project --no-annotate > GCP_billing_concierge/app_utils/.requirements.txt 2>/dev/null || \
	uv export --no-hashes --no-header --no-dev --no-emit-project > GCP_billing_concierge/app_utils/.requirements.txt) && \
	uv run -m GCP_billing_concierge.app_utils.deploy \
		--project="$(G_PROJECT)" \
		--source-packages=./GCP_billing_concierge \
		--entrypoint-module=GCP_billing_concierge.agent_engine_app \
		--entrypoint-object=agent_engine \
		--requirements-file=GCP_billing_concierge/app_utils/.requirements.txt \
		--service-account="$(AGENT_SA)" \
		$(if $(AGENT_IDENTITY),--agent-identity) \
		$(if $(filter command line,$(origin SECRETS)),--set-secrets="$(SECRETS)")
	# Trigger the secret storage 
	@$(MAKE) store_agent_id

# --- Variables ---
AGENT_ID_SECRET_NAME = billing-concierge-agent-id
METADATA_FILE = deployment_metadata.json

# --- New Target: Store Agent ID in Secret Manager ---
store_agent_id:
	@echo "🔐 Extracting Agent ID and storing in Secret Manager..."
	@G_PROJECT=$$(grep "^GOOGLE_CLOUD_PROJECT=" GCP_billing_concierge/.env | cut -d'=' -f2); \
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

