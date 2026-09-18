"""FinOps-optimized BigQuery Toolset with cost guardrails, dry-run analysis, and schema caching."""

from __future__ import annotations

import contextvars
import functools
import hashlib
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import google.oauth2.credentials
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import ToolPredicate
from google.adk.tools.bigquery import metadata_tool, query_tool
from google.adk.tools.bigquery.bigquery_credentials import BigQueryCredentialsConfig
from google.adk.tools.bigquery.bigquery_toolset import BigQueryToolset
from google.adk.tools.bigquery.config import BigQueryToolConfig, WriteMode
from google.adk.tools.google_tool import GoogleTool
from google.adk.tools.tool_context import ToolContext
from google.auth.credentials import Credentials
from typing_extensions import override

logger = logging.getLogger(__name__)

# Request-scoped user OAuth Bearer token ContextVar (populated by FastAPI middleware or client)
user_oauth_token_ctx: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "user_oauth_token_ctx", default=None
)


def set_current_user_token(token: Optional[str]) -> None:
    """Sets the current request's user OAuth token in contextvars."""
    user_oauth_token_ctx.set(token)


def get_current_user_token() -> Optional[str]:
    """Gets the current request's user OAuth token from contextvars."""
    return user_oauth_token_ctx.get()


# Standard BigQuery on-demand pricing reference ($6.25 per TB)
BIGQUERY_ON_DEMAND_PRICE_PER_TB_USD = 6.25
BYTES_PER_MB = 1024**2
BYTES_PER_GB = 1024**3
BYTES_PER_TB = 1024**4


class FinOpsGoogleTool(GoogleTool):
    """GoogleTool subclass bridging Gemini Enterprise external tokens and ADK Web interactive OAuth."""

    def __init__(
        self,
        func: Callable[..., Any],
        toolset: FinOpsBigQueryToolset,
        *,
        credentials_config: Optional[BigQueryCredentialsConfig] = None,
        tool_settings: Optional[BigQueryToolConfig] = None,
    ):
        super().__init__(
            func=func,
            credentials_config=credentials_config,
            tool_settings=tool_settings,
        )
        self.toolset = toolset

    @override
    async def run_async(
        self, *, args: dict[str, Any], tool_context: ToolContext
    ) -> Any:
        """Main entry point for tool execution with dual-mode credential handling."""
        try:
            # 1. External Token Check (Gemini Enterprise toolAuthorizations or ContextVar)
            effective_creds, cred_type = self.toolset._resolve_effective_credentials(
                tool_context, None
            )
            if effective_creds is not None and cred_type in ("user_oauth", "ambient_sa"):
                logger.info(
                    "FinOpsGoogleTool: executing %s with %s credentials.",
                    self.name,
                    cred_type,
                )
                return await self._run_async_with_credential(
                    effective_creds, self._tool_settings, args, tool_context
                )

            # 2. Interactive OAuth Check (ADK Web Dev UI via GoogleCredentialsManager)
            if self._credentials_manager:
                credentials = None
                try:
                    credentials = await self._credentials_manager.get_valid_credentials(
                        tool_context
                    )
                except Exception as e:
                    logger.warning(
                        "FinOpsGoogleTool: get_valid_credentials returned error: %s", e
                    )

                if credentials is None:
                    # Check if an auth_uri was generated in tool_context actions
                    auth_uri = None
                    if tool_context and hasattr(tool_context, "_event_actions"):
                        for cfg in tool_context._event_actions.requested_auth_configs.values():
                            if getattr(cfg, "exchanged_auth_credential", None) and getattr(
                                cfg.exchanged_auth_credential, "oauth2", None
                            ):
                                auth_uri = cfg.exchanged_auth_credential.oauth2.auth_uri
                                break

                    popup_note = (
                        "\n\n👉 **ADK Web UI Instructions:**\n"
                        "An OAuth consent popup was requested. If your browser blocked it:\n"
                        "1. Look for the 'Pop-up blocked' icon in your browser's address bar and click **'Always allow pop-ups and redirects from http://127.0.0.1:8000'** (or `http://localhost:8000`).\n"
                        "2. In Google Cloud Console (APIs & Services > Credentials > your OAuth Client ID), ensure `http://127.0.0.1:8000` and `http://localhost:8000` are listed under **Authorized redirect URIs**.\n"
                        "3. Once allowed, retry your question and complete the Google login in the popup."
                    )

                    return (
                        f"User authorization is required to access Google services for {self.name}. "
                        f"Please complete the authorization flow in your browser.{popup_note}"
                    )

                logger.info(
                    "FinOpsGoogleTool: executing %s with interactive OAuth credentials.",
                    self.name,
                )
                return await self._run_async_with_credential(
                    credentials, self._tool_settings, args, tool_context
                )

            # 3. Security Enforcement: If user OAuth is required and no credentials available, block query
            if self.toolset.require_user_oauth:
                return {
                    "status": "ERROR",
                    "error_details": (
                        "FINOPS SECURITY: Authentication Required.\n"
                        "To protect sensitive cloud billing data, BigQuery operations "
                        "require end-user OAuth authentication. No valid user credentials "
                        "were provided in the session context.\n\n"
                        "• For ADK Web (local testing): Ensure OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET "
                        "are set in your .env file so the interactive OAuth consent flow can trigger.\n"
                        "• For Gemini Enterprise: Ensure the agent has registered toolAuthorizations "
                        "matching the Authorization object (e.g., 'bq agent')."
                    ),
                }

            # 4. Fallback to Ambient Service Account credentials
            ambient = self.toolset.ambient_credentials
            if ambient:
                logger.info(
                    "FinOpsGoogleTool: executing %s with ambient credentials.", self.name
                )
                return await self._run_async_with_credential(
                    ambient, self._tool_settings, args, tool_context
                )

            return {
                "status": "ERROR",
                "error_details": "No credentials available to execute BigQuery tool.",
            }

        except Exception as ex:
            logger.exception("FinOpsGoogleTool error during %s: %s", self.name, ex)
            return {
                "status": "ERROR",
                "error_details": str(ex),
            }


class FinOpsBigQueryToolset(BigQueryToolset):
    """Enhanced BigQuery Toolset providing FinOps cost guardrails, dry-run cost calculations, and schema caching."""

    def __init__(
        self,
        *,
        tool_filter: Optional[Union[ToolPredicate, List[str]]] = None,
        credentials_config: Optional[BigQueryCredentialsConfig] = None,
        bigquery_tool_config: Optional[BigQueryToolConfig] = None,
        max_bytes_billed: Optional[int] = 1_073_741_824,  # Default 1 GiB limit
        billing_project: str = "",
        billing_dataset: str = "",
        billing_table: str = "",
        ambient_credentials: Optional[Credentials] = None,
        require_user_oauth: bool = False,
        external_access_token_key: str = "user_oauth_token",
    ):
        """Initializes FinOpsBigQueryToolset.

        Args:
            tool_filter: Optional list of tool names or predicate to expose.
            credentials_config: BigQueryCredentialsConfig instance.
            bigquery_tool_config: BigQueryToolConfig instance.
            max_bytes_billed: Hard limit in bytes for queries (defaults to 1 GiB).
            billing_project: GCP Project ID containing the billing export dataset.
            billing_dataset: BigQuery dataset ID containing billing tables.
            billing_table: BigQuery billing export table ID.
            ambient_credentials: Optional Service Account credentials for background jobs.
            require_user_oauth: If True, queries require valid user OAuth credentials.
            external_access_token_key: Key in tool_context.state to look for user OAuth token.
        """
        config = bigquery_tool_config or BigQueryToolConfig(write_mode=WriteMode.BLOCKED)
        if max_bytes_billed and config.maximum_bytes_billed is None:
            config.maximum_bytes_billed = max_bytes_billed

        super().__init__(
            tool_filter=tool_filter,
            credentials_config=credentials_config,
            bigquery_tool_config=config,
        )
        self.max_bytes_billed = config.maximum_bytes_billed
        self.billing_project = billing_project
        self.billing_dataset = billing_dataset
        self.billing_table = billing_table
        self.ambient_credentials = ambient_credentials
        self.require_user_oauth = require_user_oauth
        self.external_access_token_key = external_access_token_key
        # In-memory schema cache: (caller_key, project_id, dataset_id, table_id) -> schema dict
        self._schema_cache: Dict[Tuple[str, str, str, str], dict] = {}

    def clear_cache(self) -> None:
        """Clears the internal table schema cache."""
        self._schema_cache.clear()

    def _resolve_effective_credentials(
        self,
        tool_context: Optional[ToolContext],
        incoming_credentials: Optional[Credentials],
    ) -> Tuple[Optional[Credentials], str]:
        """Resolves the credential to use for BigQuery execution.

        Returns:
            (credentials, credential_type) where credential_type is:
            - 'user_oauth': authenticated end-user credentials (OAuth access token or user creds)
            - 'ambient_sa': service account / application default credentials
            - 'none': no credentials available
        """
        # 1. Check tool_context session state for external access token (e.g. Gemini Enterprise toolAuthorizations)
        token: Optional[str] = None
        state_obj = None
        if tool_context:
            if hasattr(tool_context, "state") and tool_context.state is not None:
                state_obj = tool_context.state
            elif hasattr(tool_context, "_invocation_context") and getattr(tool_context, "_invocation_context", None):
                inv = getattr(tool_context, "_invocation_context")
                if hasattr(inv, "session") and hasattr(inv.session, "state"):
                    state_obj = inv.session.state
                elif hasattr(inv, "state"):
                    state_obj = inv.state
            elif hasattr(tool_context, "session") and hasattr(getattr(tool_context, "session"), "state"):
                state_obj = tool_context.session.state

        state_dict: dict[str, Any] = {}
        if state_obj is not None:
            if hasattr(state_obj, "to_dict") and callable(state_obj.to_dict):
                try:
                    state_dict = state_obj.to_dict()
                except Exception as e:
                    logger.debug("Failed to call state.to_dict(): %s", e)
            elif hasattr(state_obj, "items") and callable(state_obj.items):
                try:
                    state_dict = dict(state_obj.items())
                except Exception as e:
                    logger.debug("Failed to dict(state.items()): %s", e)
            elif isinstance(state_obj, dict):
                state_dict = state_obj
            elif hasattr(state_obj, "_value") and isinstance(getattr(state_obj, "_value"), dict):
                state_dict = dict(getattr(state_obj, "_value", {}))
                if hasattr(state_obj, "_delta") and isinstance(getattr(state_obj, "_delta"), dict):
                    state_dict.update(getattr(state_obj, "_delta"))

        if state_dict:
            def _extract_str(val: Any) -> Optional[str]:
                if not val:
                    return None
                if isinstance(val, str) and val.strip():
                    return val.strip()
                # Check AuthCredential or OAuth2Auth object
                if hasattr(val, "oauth2") and getattr(val, "oauth2") is not None:
                    oauth2_obj = getattr(val, "oauth2")
                    tok = getattr(oauth2_obj, "access_token", None) or getattr(oauth2_obj, "token", None)
                    if isinstance(tok, str) and tok.strip():
                        return tok.strip()
                # Check direct token / access_token attributes
                for attr in ("access_token", "token", "accessToken", "token_value"):
                    if hasattr(val, attr):
                        attr_v = getattr(val, attr)
                        if isinstance(attr_v, str) and attr_v.strip():
                            return attr_v.strip()
                # Model dump if pydantic
                if hasattr(val, "model_dump") and callable(val.model_dump):
                    try:
                        val = val.model_dump(exclude_none=True)
                    except Exception:
                        pass
                elif hasattr(val, "to_dict") and callable(val.to_dict):
                    try:
                        val = val.to_dict()
                    except Exception:
                        pass
                if isinstance(val, dict):
                    for sub_k in ("access_token", "token", "accessToken", "bearer_token", "token_value"):
                        sub_v = val.get(sub_k)
                        if isinstance(sub_v, str) and sub_v.strip():
                            return sub_v.strip()
                    for nested_k in (
                        "oauth2",
                        "serverSideOauth2",
                        "credential",
                        "auth_credential",
                        "token",
                        "raw_auth_credential",
                        "exchanged_auth_credential",
                    ):
                        nested_v = val.get(nested_k)
                        if isinstance(nested_v, dict):
                            for sub_k in ("access_token", "token", "accessToken", "bearer_token", "token_value"):
                                sub_v = nested_v.get(sub_k)
                                if isinstance(sub_v, str) and sub_v.strip():
                                    return sub_v.strip()
                return None

            env_auth_id = os.getenv("AUTH_ID")
            env_proj = os.getenv("GOOGLE_CLOUD_PROJECT")
            env_loc = os.getenv("GOOGLE_CLOUD_REGION", "us")
            raw_agent_name = os.getenv("AGENT_NAME", "").strip()
            clean_agent = (
                re.sub(r"[^a-zA-Z0-9-]", "-", raw_agent_name).lower().strip("-")
                if raw_agent_name
                else ""
            )
            agent_auth_id = (
                f"{clean_agent}-oauth"
                if clean_agent and clean_agent not in ("gcp-billing-concierge", "billing-concierge")
                else None
            )

            candidate_keys = [
                self.external_access_token_key,
                f"temp:{self.external_access_token_key}" if self.external_access_token_key else None,
                env_auth_id,
                f"temp:{env_auth_id}" if env_auth_id else None,
                agent_auth_id,
                f"temp:{agent_auth_id}" if agent_auth_id else None,
                "billing-ge-oauth",
                "temp:billing-ge-oauth",
                "bq-agent",
                "temp:bq-agent",
                "bq agent",
                "temp:bq agent",
                "bq_agent",
                "temp:bq_agent",
                "user_oauth_token",
                "oauth_token",
                "access_token",
            ]
            if env_proj and env_auth_id:
                candidate_keys.extend([
                    f"projects/{env_proj}/locations/{env_loc}/authorizations/{env_auth_id}",
                    f"temp:projects/{env_proj}/locations/{env_loc}/authorizations/{env_auth_id}",
                ])
            # Backwards compatibility for specific Discovery Engine resource strings
            candidate_keys.extend([
                "projects/813632901865/locations/us/authorizations/bq-agent",
                "temp:projects/813632901865/locations/us/authorizations/bq-agent",
                "projects/813632901865/locations/us/authorizations/bq agent",
                "temp:projects/813632901865/locations/us/authorizations/bq agent",
            ])
            for key in candidate_keys:
                if key and key in state_dict:
                    extracted = _extract_str(state_dict[key])
                    if extracted:
                        token = extracted
                        logger.info("FinOps Auth: Token matched under candidate key '%s'", key)
                        break

            # Fallback: scan for any value in state that contains a Google OAuth access token
            if not token:
                for k, v in state_dict.items():
                    extracted = _extract_str(v)
                    if extracted:
                        if extracted.startswith("ya29.") or (
                            (k.startswith("temp:") or "oauth" in k.lower() or "auth" in k.lower() or "token" in k.lower() or "agent" in k.lower())
                            and len(extracted) > 20
                        ):
                            token = extracted
                            logger.info("FinOps Auth: Found token dynamically under state key '%s'", k)
                            break

            logger.info(
                "FinOps Auth: tool_context.state keys: %s (token found: %s)",
                list(state_dict.keys()),
                bool(token),
            )

        # 2. Check request-scoped ContextVar (e.g. from FastAPI Authorization header)
        if not token:
            token = get_current_user_token()

        if token:
            user_creds = google.oauth2.credentials.Credentials(token=token)
            return user_creds, "user_oauth"

        # 3. Check if incoming credentials is already an OAuth user credential
        if incoming_credentials and isinstance(
            incoming_credentials, google.oauth2.credentials.Credentials
        ):
            return incoming_credentials, "user_oauth"

        # 4. Check if tool_context has cached user OAuth JSON in bigquery_token_cache
        cached_json = state_dict.get("bigquery_token_cache")
        if cached_json:
            try:
                user_info = (
                    json.loads(cached_json)
                    if isinstance(cached_json, str)
                    else cached_json
                )
                creds = google.oauth2.credentials.Credentials.from_authorized_user_info(
                    user_info,
                    scopes=["https://www.googleapis.com/auth/bigquery"],
                )
                return creds, "user_oauth"
            except Exception as e:
                logger.debug("Failed to deserialize cached user credentials: %s", e)

        # 5. Check if this is an automated background audit (e.g. Cloud Scheduler)
        is_background_audit = bool(
            state_dict.get("is_background_audit")
            or state_dict.get("is_background_job")
            or state_dict.get("cloud_scheduler")
        )

        # 6. If strict user OAuth is required and not a background audit, reject
        if self.require_user_oauth and not is_background_audit:
            return None, "none"

        # 7. Fallback to ambient / SA credentials (for background jobs or when OAuth disabled)
        ambient = self.ambient_credentials or incoming_credentials
        if ambient:
            return ambient, "ambient_sa"

        return None, "none"

    def _get_caller_cache_key(
        self,
        tool_context: Optional[ToolContext],
        effective_credentials: Optional[Credentials],
    ) -> str:
        """Derives a secure caller identity key to prevent cross-user schema cache leaking."""
        if (
            effective_credentials
            and hasattr(effective_credentials, "token")
            and effective_credentials.token
        ):
            return hashlib.sha256(effective_credentials.token.encode()).hexdigest()[:16]
        if tool_context:
            user_id = getattr(tool_context, "user_id", None)
            if not user_id and hasattr(tool_context, "_invocation_context"):
                user_id = getattr(tool_context._invocation_context, "user_id", None)
            if user_id:
                return f"user:{user_id}"
        return "shared_sa"

    def _create_cached_get_table_info(self) -> Callable[..., dict]:
        """Wraps metadata_tool.get_table_info with in-memory caching and location auto-recovery."""
        def get_table_info(
            project_id: str,
            dataset_id: str,
            table_id: str,
            credentials: Optional[Credentials] = None,
            settings: Optional[BigQueryToolConfig] = None,
            tool_context: Optional[ToolContext] = None,
        ) -> dict:
            """Get metadata information about a BigQuery table."""
            # Resolve user OAuth or ambient credentials
            effective_creds, cred_type = self._resolve_effective_credentials(
                tool_context, credentials
            )
            if effective_creds is None:
                return {
                    "status": "ERROR",
                    "error_details": (
                        "FINOPS SECURITY: Authentication Required.\n"
                        "Inspection of BigQuery table metadata requires end-user OAuth credentials. "
                        "No user OAuth token was provided in the session context.\n"
                        "Please authenticate with your Google account."
                    ),
                }

            # Normalize target parameters to configured billing export if mismatched or omitted
            target_project = project_id
            target_dataset = dataset_id
            target_table = table_id

            if self.billing_project and project_id != self.billing_project:
                if dataset_id == self.billing_dataset or table_id == self.billing_table or "billing" in dataset_id:
                    logger.info(
                        "FinOps: Normalizing get_table_info project_id from %s to billing project %s",
                        project_id,
                        self.billing_project,
                    )
                    target_project = self.billing_project

            if self.billing_dataset and (not dataset_id or dataset_id == "billing_export_dataset"):
                target_dataset = self.billing_dataset

            if self.billing_table and (not table_id or "gcp_billing_export" not in table_id):
                target_table = self.billing_table

            caller_key = self._get_caller_cache_key(tool_context, effective_creds)
            cache_key = (caller_key, target_project, target_dataset, target_table)
            if cache_key in self._schema_cache:
                logger.info(
                    "FinOps Cache Hit: Reusing cached schema for %s on %s.%s.%s",
                    caller_key,
                    target_project,
                    target_dataset,
                    target_table,
                )
                return self._schema_cache[cache_key]

            logger.info(
                "FinOps: Fetching schema from BigQuery for %s.%s.%s (auth: %s)",
                target_project,
                target_dataset,
                target_table,
                cred_type,
            )
            result = metadata_tool.get_table_info(
                project_id=target_project,
                dataset_id=target_dataset,
                table_id=target_table,
                credentials=effective_creds,
                settings=settings,
            )

            # Check for IAM Permission Denied (403 Forbidden)
            if isinstance(result, dict) and result.get("status") == "ERROR":
                err_str = str(result.get("error_details", ""))
                if any(
                    sig in err_str
                    for sig in (
                        "Access Denied",
                        "Permission denied",
                        "403",
                        "User does not have permission",
                        "Permission bigquery.",
                    )
                ):
                    result["error_details"] = (
                        "FINOPS SECURITY: Access Denied (403 Forbidden).\n"
                        "Your authenticated Google account does not have permission to inspect the Cloud Billing export metadata.\n"
                        "To view table schema or query billing data, your GCP user account requires the "
                        "'roles/bigquery.dataViewer' role on the billing export dataset.\n"
                        "Please contact your GCP administrator to request access."
                    )
                    return result

            # Auto-recovery: If failed, retry with billing_project or location auto-detection
            if isinstance(result, dict) and result.get("status") == "ERROR":
                err_str = str(result.get("error_details", ""))
                if "Not found: Dataset" in err_str and self.billing_project and target_project != self.billing_project:
                    logger.warning(
                        "FinOps: Dataset not found in %s, retrying in configured billing project %s...",
                        target_project,
                        self.billing_project,
                    )
                    target_project = self.billing_project
                    cache_key = (caller_key, target_project, target_dataset, target_table)
                    result = metadata_tool.get_table_info(
                        project_id=target_project,
                        dataset_id=target_dataset,
                        table_id=target_table,
                        credentials=effective_creds,
                        settings=settings,
                    )
                    err_str = str(result.get("error_details", ""))

                if "was not found in location" in err_str:
                    logger.warning(
                        "FinOps: Location mismatch detected in get_table_info (%s). Retrying with location=None...",
                        err_str,
                    )
                    relaxed_settings = settings.model_copy(update={"location": None})
                    result = metadata_tool.get_table_info(
                        project_id=target_project,
                        dataset_id=target_dataset,
                        table_id=target_table,
                        credentials=effective_creds,
                        settings=relaxed_settings,
                    )
                    if (
                        isinstance(result, dict)
                        and result.get("status") == "ERROR"
                        and "was not found in location" in str(result.get("error_details", ""))
                    ):
                        logger.warning(
                            "FinOps: Retrying get_table_info with fallback location='US'..."
                        )
                        us_settings = settings.model_copy(update={"location": "US"})
                        result = metadata_tool.get_table_info(
                            project_id=target_project,
                            dataset_id=target_dataset,
                            table_id=target_table,
                            credentials=effective_creds,
                            settings=us_settings,
                        )

            if isinstance(result, dict) and result.get("status") != "ERROR":
                self._schema_cache[cache_key] = result
            return result

        return get_table_info

    def _create_finops_execute_sql(self) -> Callable[..., dict]:
        """Wraps query_tool.execute_sql with cost estimation, dry-run analysis, and partition advice."""
        base_execute = query_tool.get_execute_sql(self._tool_settings)

        def execute_sql(
            project_id: str,
            query: str,
            credentials: Optional[Credentials] = None,
            settings: Optional[BigQueryToolConfig] = None,
            tool_context: Optional[ToolContext] = None,
            dry_run: bool = False,
        ) -> dict:
            """Execute a SQL query on BigQuery with FinOps guardrails."""
            # Resolve user OAuth or ambient credentials
            effective_creds, cred_type = self._resolve_effective_credentials(
                tool_context, credentials
            )
            if effective_creds is None:
                return {
                    "status": "ERROR",
                    "error_details": (
                        "FINOPS SECURITY POLICY: User OAuth Authentication Required.\n"
                        "To protect sensitive cloud billing data, querying the billing export "
                        "requires end-user OAuth authentication. No valid user credentials were provided in the session context.\n"
                        "Please authenticate with your Google account that has BigQuery access."
                    ),
                }

            logger.info("FinOps BigQuery execute_sql using credential type: %s", cred_type)

            # Route execution to compute_project_id if caller passed external dataset project
            target_project = project_id
            if settings.compute_project_id and project_id != settings.compute_project_id:
                logger.info(
                    "FinOps: Routing execute_sql query execution to compute project %s (requested: %s)",
                    settings.compute_project_id,
                    project_id,
                )
                target_project = settings.compute_project_id

            # Check for common partition filters in billing queries
            is_billing_query = bool(re.search(r"billing|export", query, re.IGNORECASE))
            has_partition_filter = bool(
                re.search(
                    r"(_PARTITIONDATE|_PARTITIONTIME|export_time|usage_start_time|usage_end_time|invoice\.month)",
                    query,
                    re.IGNORECASE,
                )
            )

            # Execute underlying BigQuery tool
            result = base_execute(
                project_id=target_project,
                query=query,
                credentials=effective_creds,
                settings=settings,
                tool_context=tool_context,
                dry_run=dry_run,
            )

            # Check for IAM Permission Denied (403 Forbidden)
            if isinstance(result, dict) and result.get("status") == "ERROR":
                err_details = str(result.get("error_details", ""))
                if any(
                    sig in err_details
                    for sig in (
                        "Access Denied",
                        "Permission denied",
                        "403",
                        "User does not have permission",
                        "Permission bigquery.",
                    )
                ):
                    result["error_details"] = (
                        "FINOPS SECURITY ENFORCEMENT: Access Denied (403 Forbidden).\n"
                        "Your authenticated Google account does not have permission to query the Cloud Billing export data in BigQuery.\n"
                        "Required GCP IAM Permissions:\n"
                        "  1. 'roles/bigquery.dataViewer' (or BigQuery data read permissions) on the billing export dataset\n"
                        "  2. 'roles/bigquery.jobUser' (or bigquery.jobs.create) on the query compute project\n"
                        "Please contact your Google Cloud administrator to request billing data access."
                    )
                    return result

            # Auto-recovery: If failed due to a dataset location mismatch, retry with location=None, then 'US'
            if isinstance(result, dict) and result.get("status") == "ERROR":
                err_details = str(result.get("error_details", ""))
                if "was not found in location" in err_details:
                    logger.warning(
                        "FinOps: Location mismatch in execute_sql (%s). Retrying with location=None...",
                        err_details,
                    )
                    relaxed_settings = settings.model_copy(update={"location": None})
                    relaxed_execute = query_tool.get_execute_sql(relaxed_settings)
                    result = relaxed_execute(
                        project_id=target_project,
                        query=query,
                        credentials=effective_creds,
                        settings=relaxed_settings,
                        tool_context=tool_context,
                        dry_run=dry_run,
                    )
                    if (
                        isinstance(result, dict)
                        and result.get("status") == "ERROR"
                        and "was not found in location" in str(result.get("error_details", ""))
                    ):
                        logger.warning(
                            "FinOps: Retrying execute_sql with fallback location='US'..."
                        )
                        us_settings = settings.model_copy(update={"location": "US"})
                        us_execute = query_tool.get_execute_sql(us_settings)
                        result = us_execute(
                            project_id=target_project,
                            query=query,
                            credentials=effective_creds,
                            settings=us_settings,
                            tool_context=tool_context,
                            dry_run=dry_run,
                        )

            # Enrich dry run results with FinOps financial calculations
            if dry_run and isinstance(result, dict) and result.get("status") == "SUCCESS":
                dry_run_info = result.get("dry_run_info", {})
                stats = dry_run_info.get("statistics", {})
                bytes_str = (
                    stats.get("totalBytesProcessed")
                    or stats.get("query", {}).get("totalBytesBilled")
                    or "0"
                )
                try:
                    bytes_val = int(bytes_str)
                    bytes_mb = bytes_val / BYTES_PER_MB
                    bytes_gb = bytes_val / BYTES_PER_GB
                    est_cost_usd = (bytes_val / BYTES_PER_TB) * BIGQUERY_ON_DEMAND_PRICE_PER_TB_USD

                    finops_analysis = {
                        "estimated_bytes_processed": bytes_val,
                        "estimated_size_mb": round(bytes_mb, 2),
                        "estimated_size_gb": round(bytes_gb, 4),
                        "estimated_cost_usd": f"${est_cost_usd:.6f}",
                        "under_budget": bool(
                            not self.max_bytes_billed or bytes_val <= self.max_bytes_billed
                        ),
                        "budget_limit_gb": (
                            round(self.max_bytes_billed / BYTES_PER_GB, 2)
                            if self.max_bytes_billed
                            else None
                        ),
                    }

                    if is_billing_query and not has_partition_filter:
                        finops_analysis["finops_warning"] = (
                            "FinOps Advisory: Query lacks explicit partition filters "
                            "(_PARTITIONDATE or export_time). Consider adding partition clauses "
                            "to reduce billed bytes."
                        )

                    result["finops_analysis"] = finops_analysis
                except Exception as e:
                    logger.debug("Failed to calculate FinOps cost metrics: %s", e)

            # Intercept hard byte-limit errors to give actionable FinOps guidance
            if isinstance(result, dict) and result.get("status") == "ERROR":
                err_details = str(result.get("error_details", ""))
                if "Query exceeded limit for bytes billed" in err_details:
                    limit_gb = (
                        (self.max_bytes_billed / BYTES_PER_GB)
                        if self.max_bytes_billed
                        else 1.0
                    )
                    result["error_details"] = (
                        f"FINOPS GUARDRAIL ENFORCED: {err_details}\n"
                        f"Query was blocked because it would exceed the FinOps query budget limit of {limit_gb:.2f} GB.\n"
                        "To execute this safely, please optimize your SQL by filtering on partition columns "
                        "(e.g., `_PARTITIONDATE >= ...` or `DATE(export_time) >= ...`) to narrow the date window."
                    )

            return result

        return execute_sql

    def _create_finops_get_job_info(self) -> Callable[..., dict]:
        """Wraps metadata_tool.get_job_info with effective credential resolution."""
        def get_job_info(
            project_id: str,
            job_id: str,
            credentials: Optional[Credentials] = None,
            settings: Optional[BigQueryToolConfig] = None,
            tool_context: Optional[ToolContext] = None,
        ) -> dict:
            """Get metadata information about a BigQuery job."""
            effective_creds, _ = self._resolve_effective_credentials(
                tool_context, credentials
            )
            return metadata_tool.get_job_info(
                project_id=project_id,
                job_id=job_id,
                credentials=effective_creds,
                settings=settings,
            )

        return get_job_info

    async def get_tools(
        self, readonly_context: Optional[ReadonlyContext] = None
    ) -> List[BaseTool]:
        """Returns tools with FinOps caching and query guardrails applied."""
        cached_table_info_func = self._create_cached_get_table_info()
        finops_execute_sql_func = self._create_finops_execute_sql()
        finops_get_job_info_func = self._create_finops_get_job_info()

        all_tools = [
            FinOpsGoogleTool(
                func=func,
                toolset=self,
                credentials_config=self._credentials_config,
                tool_settings=self._tool_settings,
            )
            for func in [
                metadata_tool.get_dataset_info,
                cached_table_info_func,
                metadata_tool.list_dataset_ids,
                metadata_tool.list_table_ids,
                finops_get_job_info_func,
                finops_execute_sql_func,
                query_tool.forecast,
                query_tool.analyze_contribution,
                query_tool.detect_anomalies,
            ]
        ]

        return [
            tool
            for tool in all_tools
            if self._is_tool_selected(tool, readonly_context)
        ]
