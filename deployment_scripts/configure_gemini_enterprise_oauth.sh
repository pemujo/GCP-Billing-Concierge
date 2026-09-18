#!/usr/bin/env bash
# ==============================================================================
# Configure OAuth 2.0 for Gemini Enterprise (Discovery Engine) via REST API
#
# This script:
#  1. Creates or updates the Discovery Engine Authorization resource for BigQuery.
#  2. Retrieves the registered Gemini Enterprise agent resource path.
#  3. Patches the agent's authorizationConfig.toolAuthorizations to link the auth.
# ==============================================================================

set -euo pipefail

# --- 1. Load Environment Configuration ---
if [ -f ".env" ]; then
  # shellcheck disable=SC1091
  source .env
elif [ -f "GCP_billing_concierge/.env" ]; then
  # shellcheck disable=SC1091
  source GCP_billing_concierge/.env
fi

TARGET_AGENT_INPUT="${1:-${AGENT_NAME:-GCP_billing_concierge}}"
CLI_AUTH_INPUT="${2:-}"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
LOCATION="${DISCOVERY_ENGINE_LOCATION:-us}" # 'us', 'eu', or 'global'

# Derive dynamic, agent-name-aware default Authorization ID
CLEAN_AGENT_NAME=$(echo "${TARGET_AGENT_INPUT}" | tr '[:upper:]' '[:lower:]' | tr ' _' '--' | tr -cd 'a-z0-9-')
if [ "${CLEAN_AGENT_NAME}" = "gcp-billing-concierge" ] || [ "${CLEAN_AGENT_NAME}" = "billing-concierge" ] || [ -z "${CLEAN_AGENT_NAME}" ]; then
  DEFAULT_AGENT_AUTH_ID="billing-ge-oauth"
else
  DEFAULT_AGENT_AUTH_ID="${CLEAN_AGENT_NAME}-oauth"
fi

if [ -n "${CLI_AUTH_INPUT}" ]; then
  DEFAULT_AUTH_SUGGESTION="${CLI_AUTH_INPUT}"
elif [ -n "${AUTH_ID:-}" ]; then
  # If existing AUTH_ID in .env is the generic default ('billing-ge-oauth' or 'bq-agent')
  # but the agent being configured is a custom agent, prefer the agent-aware default
  if { [ "${AUTH_ID}" = "billing-ge-oauth" ] || [ "${AUTH_ID}" = "bq-agent" ]; } && [ "${DEFAULT_AGENT_AUTH_ID}" != "billing-ge-oauth" ]; then
    DEFAULT_AUTH_SUGGESTION="${DEFAULT_AGENT_AUTH_ID}"
  else
    DEFAULT_AUTH_SUGGESTION="${AUTH_ID}"
  fi
else
  DEFAULT_AUTH_SUGGESTION="${DEFAULT_AGENT_AUTH_ID}"
fi
OAUTH_CLIENT_ID="${OAUTH_CLIENT_ID:-}"
OAUTH_CLIENT_SECRET="${OAUTH_CLIENT_SECRET:-}"

echo "================================================================="
echo " Gemini Enterprise OAuth Configuration via REST API"
echo "================================================================="

if [ -z "${PROJECT_ID}" ]; then
  read -rp "Enter Google Cloud Project ID: " PROJECT_ID
fi

# Determine numeric Project Number (strictly required by Discovery Engine Authorization paths)
PROJECT_NUMBER="${GOOGLE_CLOUD_PROJECT_NUMBER:-${PROJECT_NUMBER:-}}"
if [ -z "${PROJECT_NUMBER}" ]; then
  echo "🔍 Fetching numeric Project Number for ${PROJECT_ID}..."
  PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)" 2>/dev/null || true)
fi

if [ -z "${PROJECT_NUMBER}" ]; then
  read -rp "Enter numeric Google Cloud Project Number (e.g. 813632901865): " PROJECT_NUMBER
fi

echo "✅ Google Cloud Project ID:     ${PROJECT_ID}"
echo "✅ Google Cloud Project Number: ${PROJECT_NUMBER}"
echo "✅ Discovery Engine Location:   ${LOCATION}"

# Set Endpoint Prefix based on location
if [ "${LOCATION}" = "global" ]; then
  ENDPOINT_PREFIX=""
else
  ENDPOINT_PREFIX="${LOCATION}-"
fi

# Obtain OAuth token for Google API calls
echo "🔑 Acquiring Google Cloud access token..."
TOKEN=$(gcloud auth print-access-token 2>/dev/null || gcloud auth application-default print-access-token 2>/dev/null)
if [ -z "${TOKEN}" ]; then
  echo "❌ Error: Could not obtain gcloud access token. Please run 'gcloud auth login' and try again."
  exit 1
fi

# --- 2. Select or Configure Authorization ID (AUTH_ID) ---
echo ""
echo "🔍 Checking Discovery Engine for existing Authorization resources..."
AUTH_LIST_JSON=$(curl -s \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${ENDPOINT_PREFIX}discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_NUMBER}/locations/${LOCATION}/authorizations" 2>/dev/null || echo "{}")

EXISTING_AUTHS=$(echo "${AUTH_LIST_JSON}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for a in data.get('authorizations', []):
        name = a.get('name', '')
        aid = name.split('/')[-1]
        if aid:
            print(aid)
except Exception:
    pass
")

NUM_AUTHS=$(echo -n "${EXISTING_AUTHS}" | grep -c . || true)

AUTH_ID=""
IS_NEW_AUTH=false

if [ "${NUM_AUTHS}" -gt 0 ]; then
  echo ""
  echo "Found existing Discovery Engine Authorization resources in project:"
  auth_options=()
  idx=1
  DEFAULT_CHOICE=""

  while IFS= read -r aid; do
    [ -z "${aid}" ] && continue
    match_note=""
    if [ "${aid}" = "${DEFAULT_AUTH_SUGGESTION}" ]; then
      DEFAULT_CHOICE="${idx}"
      match_note=" [Matches target agent '${TARGET_AGENT_INPUT}']"
    fi
    echo "  [${idx}] ${aid}${match_note}"
    auth_options[${idx}]="${aid}"
    idx=$((idx + 1))
  done <<< "${EXISTING_AUTHS}"

  echo "  [c] Enter a custom / new Authorization ID"
  echo ""

  if [ -n "${DEFAULT_CHOICE}" ]; then
    read -rp "Select Authorization resource [1-$((idx - 1)), or 'c', default: ${DEFAULT_CHOICE} (${auth_options[${DEFAULT_CHOICE}]})]: " sel_auth || true
    sel_auth="${sel_auth:-${DEFAULT_CHOICE}}"
  else
    read -rp "Select Authorization resource [1-$((idx - 1)), or 'c' to create '${DEFAULT_AUTH_SUGGESTION}', default: c]: " sel_auth || true
    sel_auth="${sel_auth:-c}"
  fi

  if [ "${sel_auth}" = "c" ] || [ "${sel_auth}" = "C" ]; then
    read -rp "Enter new Authorization ID [default: ${DEFAULT_AUTH_SUGGESTION}]: " custom_auth || true
    AUTH_ID="${custom_auth:-${DEFAULT_AUTH_SUGGESTION}}"
    IS_NEW_AUTH=true
  elif [[ "${sel_auth}" =~ ^[0-9]+$ ]] && [ "${sel_auth}" -ge 1 ] && [ "${sel_auth}" -lt "${idx}" ]; then
    AUTH_ID="${auth_options[${sel_auth}]}"
  else
    AUTH_ID="${sel_auth}"
    IS_IN_EXISTING=false
    for aid in "${auth_options[@]}"; do
      if [ "${aid}" = "${AUTH_ID}" ]; then
        IS_IN_EXISTING=true
        break
      fi
    done
    if [ "${IS_IN_EXISTING}" = false ]; then
      IS_NEW_AUTH=true
    fi
  fi
else
  echo ""
  read -rp "Enter Discovery Engine Authorization ID [default: ${DEFAULT_AUTH_SUGGESTION}]: " input_auth || true
  AUTH_ID="${input_auth:-${DEFAULT_AUTH_SUGGESTION}}"
  IS_NEW_AUTH=true
fi

AUTH_ID="${AUTH_ID:-${DEFAULT_AUTH_SUGGESTION}}"
AUTH_ID="${AUTH_ID:-${DEFAULT_AGENT_AUTH_ID}}"
echo "✅ Using Authorization ID:     ${AUTH_ID} (scoped to agent '${TARGET_AGENT_INPUT}')"

# Sync chosen AUTH_ID to .env and GCP_billing_concierge/.env
python3 -c "
import sys, re
from pathlib import Path
auth_id = sys.argv[1]
for p in [Path('.env'), Path('GCP_billing_concierge/.env')]:
    if p.exists():
        content = p.read_text(encoding='utf-8')
        if re.search(r'^AUTH_ID=', content, flags=re.M):
            new_content = re.sub(r'^AUTH_ID=.*$', f'AUTH_ID=\"{auth_id}\"', content, flags=re.M)
        else:
            new_content = content.rstrip() + f'\nAUTH_ID=\"{auth_id}\"\n'
        p.write_text(new_content, encoding='utf-8')
" "${AUTH_ID}" 2>/dev/null || true

# Check OAuth Credentials
if [ -z "${OAUTH_CLIENT_ID}" ]; then
  if [ "${IS_NEW_AUTH}" = true ]; then
    read -rp "Enter OAuth 2.0 Client ID: " OAUTH_CLIENT_ID
  else
    read -rp "Enter OAuth 2.0 Client ID (press Enter to use existing authorization as-is): " OAUTH_CLIENT_ID || true
  fi
fi

if [ -n "${OAUTH_CLIENT_ID}" ] && [ -z "${OAUTH_CLIENT_SECRET}" ]; then
  read -rsp "Enter OAuth 2.0 Client Secret: " OAUTH_CLIENT_SECRET
  echo ""
fi

AUTH_RESOURCE_NAME="projects/${PROJECT_NUMBER}/locations/${LOCATION}/authorizations/${AUTH_ID}"

# --- 3. Step 1: Create or Update Authorization Resource ---
echo ""
echo "🚀 [Step 1/3] Creating/Updating Discovery Engine Authorization Resource: '${AUTH_ID}'..."

if [ -n "${OAUTH_CLIENT_ID}" ] && [ -n "${OAUTH_CLIENT_SECRET}" ]; then
  REDIRECT_URI_ENCODED="https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fstatic%2Foauth%2Foauth.html"
  SCOPES_ENCODED="https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fbigquery+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.email+https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.profile"

  AUTH_URI="https://accounts.google.com/o/oauth2/v2/auth?client_id=${OAUTH_CLIENT_ID}&redirect_uri=${REDIRECT_URI_ENCODED}&scope=${SCOPES_ENCODED}&include_granted_scopes=true&response_type=code&access_type=offline&prompt=consent"

  PAYLOAD_FILE=$(mktemp)
  cat <<AUTH_JSON > "${PAYLOAD_FILE}"
{
  "name": "${AUTH_RESOURCE_NAME}",
  "serverSideOauth2": {
    "clientId": "${OAUTH_CLIENT_ID}",
    "clientSecret": "${OAUTH_CLIENT_SECRET}",
    "authorizationUri": "${AUTH_URI}",
    "tokenUri": "https://oauth2.googleapis.com/token"
  }
}
AUTH_JSON

  AUTH_HTTP_CODE=$(curl -s -o /tmp/auth_resp.json -w "%{http_code}" \
    -X POST \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    "https://${ENDPOINT_PREFIX}discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_NUMBER}/locations/${LOCATION}/authorizations?authorizationId=${AUTH_ID}" \
    -d @"${PAYLOAD_FILE}")

  rm -f "${PAYLOAD_FILE}"

  if [ "${AUTH_HTTP_CODE}" = "200" ] || [ "${AUTH_HTTP_CODE}" = "201" ]; then
    echo "✅ Authorization resource created successfully."
  elif [ "${AUTH_HTTP_CODE}" = "409" ]; then
    echo "ℹ️ Authorization resource '${AUTH_ID}' already exists (HTTP 409). Continuing..."
  else
    echo "⚠️ Note: Authorization creation returned HTTP ${AUTH_HTTP_CODE}. Response:"
    cat /tmp/auth_resp.json
    echo ""
  fi
else
  echo "ℹ️ Reusing existing Authorization resource '${AUTH_ID}'. Skipping creation step."
fi

# --- 4. Step 2: Locate Registered Gemini Enterprise Agent ---
echo ""
echo "🔍 [Step 2/3] Locating Gemini Enterprise (Discovery Engine) registered apps and agents..."

# 2a. Discover or Select Discovery Engine / Gemini Enterprise App
ENGINES_JSON=$(curl -s \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${ENDPOINT_PREFIX}discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${LOCATION}/collections/default_collection/engines")

ENGINE_LIST=$(echo "${ENGINES_JSON}" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for e in data.get('engines', []):
        eid = e.get('name', '').split('/')[-1]
        disp = e.get('displayName', eid)
        if eid:
            print(f'{eid}\t{disp}')
except Exception:
    pass
")

APP_ID=""
NUM_ENGINES=$(echo -n "${ENGINE_LIST}" | grep -c . || true)

if [ "${NUM_ENGINES}" -eq 0 ]; then
  echo "⚠️ Could not auto-detect Gemini Enterprise APP_ID from Discovery Engine."
  read -rp "Please enter your Gemini Enterprise App/Engine ID: " APP_ID
elif [ "${NUM_ENGINES}" -eq 1 ]; then
  APP_ID=$(echo "${ENGINE_LIST}" | head -n 1 | cut -f1)
  ENGINE_DISP=$(echo "${ENGINE_LIST}" | head -n 1 | cut -f2)
  echo "✅ Using Gemini Enterprise App: ${APP_ID} (${ENGINE_DISP})"
else
  echo "Found multiple Gemini Enterprise Apps / Engines:"
  engine_map=()
  idx=1
  while IFS=$'\t' read -r eid disp; do
    echo "  [${idx}] ${eid} (${disp})"
    engine_map[${idx}]="${eid}"
    idx=$((idx + 1))
  done <<< "${ENGINE_LIST}"
  read -rp "Select Gemini Enterprise App [1-$((idx - 1))]: " sel_engine
  APP_ID="${engine_map[${sel_engine}]:-${engine_map[1]}}"
  echo "✅ Selected App ID: ${APP_ID}"
fi

if [ -z "${APP_ID}" ]; then
  echo "❌ Error: App ID is required."
  exit 1
fi

# 2b. List Agents in the App
AGENTS_JSON=$(curl -s \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${ENDPOINT_PREFIX}discoveryengine.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${LOCATION}/collections/default_collection/engines/${APP_ID}/assistants/default_assistant/agents")

AGENT_LIST=$(echo "${AGENTS_JSON}" | python3 -c "
import sys, json, re
try:
    data = json.load(sys.stdin)
    target = '${TARGET_AGENT_INPUT}'.strip()
    target_clean = re.sub(r'[^a-zA-Z0-9]', '', target).lower() if target else ''

    for a in data.get('agents', []):
        res_name = a.get('name', '')
        aid = res_name.split('/')[-1]
        disp = a.get('displayName', aid)
        aid_clean = re.sub(r'[^a-zA-Z0-9]', '', aid).lower()
        disp_clean = re.sub(r'[^a-zA-Z0-9]', '', disp).lower()
        matched = 'true' if target_clean and (target_clean in aid_clean or target_clean in disp_clean) else 'false'
        print(f'{aid}\t{disp}\t{res_name}\t{matched}')
except Exception:
    pass
")

NUM_AGENTS=$(echo -n "${AGENT_LIST}" | grep -c . || true)
AGENT_RESOURCE_NAME=""
SELECTED_AGENT_ID=""
SELECTED_DISPLAY_NAME=""

if [ "${NUM_AGENTS}" -gt 0 ]; then
  echo ""
  echo "Registered Agents found in '${APP_ID}':"
  agent_ids=()
  agent_disps=()
  agent_res=()
  idx=1
  DEFAULT_CHOICE=""

  while IFS=$'\t' read -r aid disp res matched; do
    match_tag=""
    if [ "${matched}" = "true" ] && [ -z "${DEFAULT_CHOICE}" ]; then
      DEFAULT_CHOICE="${idx}"
      match_tag=" [Matches target: '${TARGET_AGENT_INPUT}']"
    fi
    echo "  [${idx}] ${aid} (Display Name: ${disp})${match_tag}"
    agent_ids[${idx}]="${aid}"
    agent_disps[${idx}]="${disp}"
    agent_res[${idx}]="${res}"
    idx=$((idx + 1))
  done <<< "${AGENT_LIST}"

  echo "  [m] Manually enter an agent resource name or ID"
  echo ""

  PROMPT_DEFAULT="${DEFAULT_CHOICE}"
  if [ -z "${PROMPT_DEFAULT}" ]; then
    if [ -n "${TARGET_AGENT_INPUT}" ]; then
      echo "⚠️ Note: Target agent '${TARGET_AGENT_INPUT}' was NOT found in this app."
    fi
  fi

  if [ -n "${PROMPT_DEFAULT}" ]; then
    read -rp "Select agent to link OAuth to [default: ${PROMPT_DEFAULT} (${agent_ids[${PROMPT_DEFAULT}]})]: " sel_agent
    sel_agent="${sel_agent:-${PROMPT_DEFAULT}}"
  else
    read -rp "Select agent to link OAuth to [1-$((idx - 1)), or 'm']: " sel_agent
  fi

  if [ "${sel_agent}" = "m" ] || [ "${sel_agent}" = "M" ]; then
    read -rp "Enter the full agent resource name (projects/.../agents/...): " AGENT_RESOURCE_NAME
    SELECTED_AGENT_ID=$(echo "${AGENT_RESOURCE_NAME}" | sed 's/.*agents\///')
    SELECTED_DISPLAY_NAME="${SELECTED_AGENT_ID}"
  elif [ -n "${agent_res[${sel_agent}]:-}" ]; then
    AGENT_RESOURCE_NAME="${agent_res[${sel_agent}]}"
    SELECTED_AGENT_ID="${agent_ids[${sel_agent}]}"
    SELECTED_DISPLAY_NAME="${agent_disps[${sel_agent}]}"
  else
    echo "❌ Invalid selection."
    exit 1
  fi
else
  echo "⚠️ No registered agents found automatically in app '${APP_ID}'."
  read -rp "Enter the full agent resource name (projects/.../agents/...): " AGENT_RESOURCE_NAME
  SELECTED_AGENT_ID=$(echo "${AGENT_RESOURCE_NAME}" | sed 's/.*agents\///')
  SELECTED_DISPLAY_NAME="${SELECTED_AGENT_ID}"
fi

if [ -z "${AGENT_RESOURCE_NAME}" ]; then
  echo "❌ Error: Agent resource name is required."
  exit 1
fi

echo ""
echo "-----------------------------------------------------------------"
echo " Confirm OAuth Linking:"
echo "   • Target Agent:      ${SELECTED_DISPLAY_NAME} (${SELECTED_AGENT_ID})"
echo "   • Agent Resource:    ${AGENT_RESOURCE_NAME}"
echo "   • Authorization:     ${AUTH_RESOURCE_NAME}"
echo "-----------------------------------------------------------------"
read -rp "Proceed with linking OAuth to this agent? [Y/n]: " confirm_link
confirm_link="${confirm_link:-y}"
if [ "${confirm_link}" != "y" ] && [ "${confirm_link}" != "Y" ]; then
  echo "🚫 Aborted by user. No changes were made."
  exit 0
fi

# --- 5. Step 3: Link Authorization to the Agent ---
echo ""
echo "🔗 [Step 3/3] Linking Authorization '${AUTH_ID}' to Agent..."

PATCH_FILE=$(mktemp)
cat <<PATCH_JSON > "${PATCH_FILE}"
{
  "authorizationConfig": {
    "toolAuthorizations": [
      "${AUTH_RESOURCE_NAME}"
    ]
  }
}
PATCH_JSON

PATCH_HTTP_CODE=$(curl -s -o /tmp/patch_resp.json -w "%{http_code}" \
  -X PATCH \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "https://${ENDPOINT_PREFIX}discoveryengine.googleapis.com/v1alpha/${AGENT_RESOURCE_NAME}?updateMask=authorizationConfig" \
  -d @"${PATCH_FILE}")

rm -f "${PATCH_FILE}"

if [ "${PATCH_HTTP_CODE}" = "200" ]; then
  echo "🎉 SUCCESS! OAuth Authorization linked to Gemini Enterprise Agent!"
  echo ""
  echo "📋 Updated Agent Configuration:"
  cat /tmp/patch_resp.json | grep -A 5 "authorizationConfig" || cat /tmp/patch_resp.json
  echo ""
  echo "👉 Next Step: In Google Cloud Console (APIs & Services > Credentials), verify that"
  echo "   'https://vertexaisearch.cloud.google.com/static/oauth/oauth.html' is in the"
  echo "   Authorized Redirect URIs of OAuth Client ID: ${OAUTH_CLIENT_ID}"
else
  echo "❌ Error: PATCH failed with HTTP ${PATCH_HTTP_CODE}. Response:"
  cat /tmp/patch_resp.json
  exit 1
fi
