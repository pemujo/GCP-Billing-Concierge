"""FinOps-optimized BigQuery Toolset with cost guardrails, dry-run analysis, and schema caching."""

from __future__ import annotations

import functools
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

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

logger = logging.getLogger(__name__)

# Standard BigQuery on-demand pricing reference ($6.25 per TB)
BIGQUERY_ON_DEMAND_PRICE_PER_TB_USD = 6.25
BYTES_PER_MB = 1024**2
BYTES_PER_GB = 1024**3
BYTES_PER_TB = 1024**4


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
        # In-memory schema cache: (project_id, dataset_id, table_id) -> schema dict
        self._schema_cache: Dict[Tuple[str, str, str], dict] = {}

    def clear_cache(self) -> None:
        """Clears the internal table schema cache."""
        self._schema_cache.clear()

    def _create_cached_get_table_info(self) -> Callable[..., dict]:
        """Wraps metadata_tool.get_table_info with in-memory caching and location auto-recovery."""
        @functools.wraps(metadata_tool.get_table_info)
        def get_table_info(
            project_id: str,
            dataset_id: str,
            table_id: str,
            credentials: Credentials,
            settings: BigQueryToolConfig,
        ) -> dict:
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

            cache_key = (target_project, target_dataset, target_table)
            if cache_key in self._schema_cache:
                logger.info(
                    "FinOps Cache Hit: Reusing cached schema for %s.%s.%s",
                    target_project,
                    target_dataset,
                    target_table,
                )
                return self._schema_cache[cache_key]

            logger.info(
                "FinOps Cache Miss: Fetching schema from BigQuery for %s.%s.%s",
                target_project,
                target_dataset,
                target_table,
            )
            result = metadata_tool.get_table_info(
                project_id=target_project,
                dataset_id=target_dataset,
                table_id=target_table,
                credentials=credentials,
                settings=settings,
            )

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
                    cache_key = (target_project, target_dataset, target_table)
                    result = metadata_tool.get_table_info(
                        project_id=target_project,
                        dataset_id=target_dataset,
                        table_id=target_table,
                        credentials=credentials,
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
                        credentials=credentials,
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
                            credentials=credentials,
                            settings=us_settings,
                        )

            if isinstance(result, dict) and result.get("status") != "ERROR":
                self._schema_cache[cache_key] = result
            return result

        return get_table_info

    def _create_finops_execute_sql(self) -> Callable[..., dict]:
        """Wraps query_tool.execute_sql with cost estimation, dry-run analysis, and partition advice."""
        base_execute = query_tool.get_execute_sql(self._tool_settings)

        @functools.wraps(base_execute)
        def execute_sql(
            project_id: str,
            query: str,
            credentials: Credentials,
            settings: BigQueryToolConfig,
            tool_context: ToolContext,
            dry_run: bool = False,
        ) -> dict:
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
                credentials=credentials,
                settings=settings,
                tool_context=tool_context,
                dry_run=dry_run,
            )

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
                        credentials=credentials,
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
                            credentials=credentials,
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

    async def get_tools(
        self, readonly_context: Optional[ReadonlyContext] = None
    ) -> List[BaseTool]:
        """Returns tools with FinOps caching and query guardrails applied."""
        cached_table_info_func = self._create_cached_get_table_info()
        finops_execute_sql_func = self._create_finops_execute_sql()

        all_tools = [
            GoogleTool(
                func=func,
                credentials_config=self._credentials_config,
                tool_settings=self._tool_settings,
            )
            for func in [
                metadata_tool.get_dataset_info,
                cached_table_info_func,
                metadata_tool.list_dataset_ids,
                metadata_tool.list_table_ids,
                metadata_tool.get_job_info,
                finops_execute_sql_func,
                query_tool.forecast,
                query_tool.analyze_contribution,
                query_tool.detect_anomalies,
                metadata_tool.list_dataset_ids,
            ]
        ]

        return [
            tool
            for tool in all_tools
            if self._is_tool_selected(tool, readonly_context)
        ]
