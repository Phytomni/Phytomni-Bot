# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Profile and network nodes for the DeepGenome workflow.

Exports DeepGenomeProfileMixin plus cache helpers for gene symbols and
annotations. The mixin retrieves BI annotation/network data, literature
context, and Part 1 profile summaries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.http import JsonPostRetry, require_json_object
from ...config.defaults import DeepGenomeConfig
from ...func_cache import LONG_TTL_SECONDS, func_cache
from ...runtime.workflow_mixins import WorkflowMixinBase
from ..shared.sql import bi_query, sql_literal

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = Dict[str, Any]

logger = logging.getLogger(__name__)

_LOOKUP_CONFIG = DeepGenomeConfig()
# Gene-id ↔ symbol and gene annotation rows from the BI gateway are
# reference data: a deploy may push a new build now and then, but the
# per-gene mappings change on a months-to-years cadence. Reuse the
# shared long TTL so identical lookups hit the local SQLite cache
# instead of re-paying the BI gateway roundtrip for ~90 days; operators
# can drop the entries early via the phytomni-cache CLI when a fresh
# annotation pipeline lands.
GENE_LOOKUP_CACHE_TTL = LONG_TTL_SECONDS


async def _post_bi_sql(
    bi_url: str,
    sql_headers: Dict[str, str],
    sql: str,
    timeout: float = _LOOKUP_CONFIG.TIMEOUT,
) -> Dict[str, Any]:
    """Run one BI SQL query and return the JSON payload.

    Routes through the shared ``post_json_with_retries`` helper for
    parity with brief_gene's BI path (transient-transport and
    retriable-status backoff), then layers a non-JSON guard on top
    because the helper calls ``response.json()`` with no content-type
    check of its own.

    Args:
        bi_url: BI endpoint URL.
        sql_headers: HTTP headers (content type and BI token).
        sql: SQL statement to execute.
        timeout: Per-request timeout in seconds.

    Returns:
        The decoded BI JSON payload.

    Raises:
        McpError: If the BI endpoint keeps failing after all retries,
            returns no payload, or returns a 2xx body that is not valid
            JSON (e.g. an HTML 502/504 gateway page) — surfaced with a
            clear message instead of the opaque ``Expecting value:
            line 1 column 1 (char 0)``.
    """
    try:
        data = await bi_query(
            sql,
            bi_url=bi_url,
            headers=sql_headers,
            retry=JsonPostRetry(
                timeout=timeout,
                max_retries=_LOOKUP_CONFIG.MAX_RETRIES,
                retriable_codes=list(_LOOKUP_CONFIG.RETRIABLE_CODES),
                message="BI query failed",
                network_message="BI query network error",
            ),
        )
    except ValueError as exc:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=(
                    "BI backend returned non-JSON "
                    f"(2xx body is not valid JSON: {exc})"
                ),
            )
        ) from exc
    return require_json_object(
        data, "BI query returned no payload after all retries"
    )


@func_cache(
    key_params=["bi_url", "species_code", "gene_id"],
    ttl=GENE_LOOKUP_CACHE_TTL,
    exclude_params=["sql_headers"],
)
async def _cached_gene_symbol_lookup(
    bi_url: str,
    sql_headers: Dict[str, str],
    species_code: str,
    gene_id: str,
    timeout: float = _LOOKUP_CONFIG.TIMEOUT,
) -> List[str]:
    """Retrieve and cache gene symbols for one species/gene pair."""
    sql = (
        f"SELECT * FROM id_table WHERE gene_id = {sql_literal(gene_id)} "
        f"AND species_code = {sql_literal(species_code)}"
    )
    response = await _post_bi_sql(bi_url, sql_headers, sql, timeout)
    gene_symbol_list: List[str] = []
    if response["data"][0]["symbol"] is not None:
        cell_raw_value = response["data"][0]["symbol"]
        if "|" in cell_raw_value:
            gene_symbol_list.extend(set(cell_raw_value.split("|")))
        elif "," in cell_raw_value:
            gene_symbol_list.extend(set(cell_raw_value.split(",")))
        else:
            gene_symbol_list.append(cell_raw_value)
        return gene_symbol_list
    return []


@func_cache(
    key_params=["bi_url", "species_code", "gene_id"],
    ttl=GENE_LOOKUP_CACHE_TTL,
    exclude_params=["sql_headers"],
)
async def _cached_gene_annotation_lookup(
    bi_url: str,
    sql_headers: Dict[str, str],
    species_code: str,
    gene_id: str,
    timeout: float = _LOOKUP_CONFIG.TIMEOUT,
) -> Dict[str, Any]:
    """Retrieve and cache gene annotations for one species/gene pair."""
    gene_literal = sql_literal(gene_id)
    species_literal = sql_literal(species_code)
    sql_list = (
        "SELECT description FROM annotation_gene_description "
        f"WHERE gene_id = {gene_literal} "
        f"AND species_code = {species_literal}",
        "SELECT go_id, go_name FROM annotation_gene_ontology WHERE "
        f"gene_id = {gene_literal} "
        f"AND species_code = {species_literal}",
        "SELECT interpro_id, interpro_name "
        "FROM annotation_gene_interpro "
        f"WHERE gene_id = {gene_literal} "
        f"AND species_code = {species_literal}",
        "SELECT mapman, mapman_description "
        "FROM annotation_gene_mapman "
        f"WHERE gene_id = {gene_literal} "
        f"AND species_code = {species_literal}",
    )
    responses = [
        await _post_bi_sql(bi_url, sql_headers, sql, timeout)
        for sql in sql_list
    ]
    gene_anno_dict: Dict[str, Any] = {}
    if responses[0]["data"]:
        gene_anno_dict.update({"description": responses[0]["data"]})
    if responses[1]["data"]:
        gene_anno_dict.update({"go": responses[1]["data"]})
    if responses[2]["data"]:
        gene_anno_dict.update({"interpro": responses[2]["data"]})
    if responses[3]["data"]:
        gene_anno_dict.update({"mapman": responses[3]["data"]})
    return gene_anno_dict


def clear_gene_lookup_caches() -> None:
    """Clear cached gene symbol and annotation lookup results."""
    _cached_gene_symbol_lookup.cache_clear()
    _cached_gene_annotation_lookup.cache_clear()


class DeepGenomeProfileMixin(WorkflowMixinBase):
    """Gene annotation, network, and Part 1 profile nodes."""

    async def _gene_symbol(
        self: Any,
        species_code: str,
        gene_id: str,
        semaphore: Optional[asyncio.Semaphore] = None,
    ) -> List[str]:
        """Retrieve gene symbols for a specific gene ID and species code.

        This function queries a database using the `nl2sql` service to find
        gene symbols associated with the provided `gene_id` and `species_code`.
        It parses the response from `nl2sql`, expecting a specific structure,
        and extracts gene symbols. It can handle symbols that are
        pipe-separated or comma-separated within a single field. An optional
        semaphore can limit concurrency.

        Args:
            species_code: The species code for the gene for which symbols are
                being retrieved.
            gene_id: The identifier of the gene for which symbols are being
                retrieved.
            workspace_id: Identifier for the workspace containing the data.
            subject_id: Identifier for the database subject or schema.
            dialog_id: Identifier for the current dialog or conversation.
            need_insight: Whether to generate insights based on query results.
            timeout: Request timeout in seconds for the underlying `nl2sql` API
                calls.
            retriable_codes: HTTP status codes that trigger a retry.
            max_retries: Maximum number of retry attempts.
            semaphore: An optional `asyncio.Semaphore` instance to limit the
                concurrency of `nl2sql` calls if this function is called
                multiple times concurrently.

        Returns:
            A list of gene symbols associated with the given `gene_id` and
            `species_code`. Returns an empty list if no symbols are found or
            if the data format from the `nl2sql` service is not as expected.

        Raises:
            McpError: If the underlying `nl2sql` call fails after all retry
            attempts.
        """

        async def get_gene_symbol() -> List[str]:
            return await _cached_gene_symbol_lookup(
                bi_url=self.deep_genome_config.BI_URL,
                sql_headers=self._sql_headers,
                species_code=species_code,
                gene_id=gene_id,
                timeout=self.deep_genome_config.TIMEOUT,
            )

        if semaphore is not None:
            async with semaphore:
                return await get_gene_symbol()
        else:
            return await get_gene_symbol()

    async def _gene_annotation(
        self: Any,
        species_code: str,
        gene_id: str,
        semaphore: Optional[asyncio.Semaphore] = None,
    ):
        async def get_gene_annotation() -> Dict:
            return await _cached_gene_annotation_lookup(
                bi_url=self.deep_genome_config.BI_URL,
                sql_headers=self._sql_headers,
                species_code=species_code,
                gene_id=gene_id,
                timeout=self.deep_genome_config.TIMEOUT,
            )

        if semaphore is not None:
            async with semaphore:
                return await get_gene_annotation()
        else:
            return await get_gene_annotation()
