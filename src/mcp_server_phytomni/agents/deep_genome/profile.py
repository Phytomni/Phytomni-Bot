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
from typing import TYPE_CHECKING, Any

from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.http import (
    JsonPostRetry,
    require_json_object,
    resolve_request_timeout,
)
from ...config.defaults import DeepGenomeConfig
from ..shared.sql import bi_query, sql_literal

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = dict[str, Any]

logger = logging.getLogger(__name__)

_LOOKUP_CONFIG = DeepGenomeConfig()


async def _post_bi_sql(
    sql: str,
    request_timeout: float = _LOOKUP_CONFIG.TIMEOUT,
    **options: Any,
) -> dict[str, Any]:
    """Run one BI SQL query and return the JSON payload.

    Routes through the shared ``bi_query`` seam (a direct GaussDB query,
    or the relay route in customer relay mode), then layers a non-JSON
    guard on top so a 2xx body that is not valid JSON surfaces as a
    clear ``McpError`` instead of an opaque decode error.

    Args:
        sql: SQL statement to execute.
        request_timeout: Per-request timeout in seconds.
        **options: Backward-compatible ``timeout=`` keyword support.

    Returns:
        The decoded BI JSON payload.

    Raises:
        McpError: If the BI query keeps failing after all retries,
            returns no payload, or returns a 2xx body that is not valid
            JSON (e.g. an HTML 502/504 gateway page) — surfaced with a
            clear message instead of the opaque ``Expecting value:
            line 1 column 1 (char 0)``.
    """
    timeout = resolve_request_timeout(request_timeout, options)
    if timeout is None:
        timeout = _LOOKUP_CONFIG.TIMEOUT
    try:
        data = await bi_query(
            sql,
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


async def _cached_gene_symbol_lookup(
    species_code: str,
    gene_id: str,
    request_timeout: float = _LOOKUP_CONFIG.TIMEOUT,
    **options: Any,
) -> list[str]:
    """Retrieve gene symbols for one species/gene pair."""
    timeout = resolve_request_timeout(request_timeout, options)
    if timeout is None:
        timeout = _LOOKUP_CONFIG.TIMEOUT
    sql = (
        f"SELECT * FROM id_table WHERE gene_id = {sql_literal(gene_id)} "
        f"AND species_code = {sql_literal(species_code)}"
    )
    response = await _post_bi_sql(sql, timeout)
    gene_symbol_list: list[str] = []
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


async def _cached_gene_annotation_lookup(
    species_code: str,
    gene_id: str,
    request_timeout: float = _LOOKUP_CONFIG.TIMEOUT,
    **options: Any,
) -> dict[str, Any]:
    """Retrieve gene annotations for one species/gene pair."""
    timeout = resolve_request_timeout(request_timeout, options)
    if timeout is None:
        timeout = _LOOKUP_CONFIG.TIMEOUT
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
    responses = [await _post_bi_sql(sql, timeout) for sql in sql_list]
    gene_anno_dict: dict[str, Any] = {}
    if responses[0]["data"]:
        gene_anno_dict.update({"description": responses[0]["data"]})
    if responses[1]["data"]:
        gene_anno_dict.update({"go": responses[1]["data"]})
    if responses[2]["data"]:
        gene_anno_dict.update({"interpro": responses[2]["data"]})
    if responses[3]["data"]:
        gene_anno_dict.update({"mapman": responses[3]["data"]})
    return gene_anno_dict


class DeepGenomeProfileMixin:
    """Gene annotation, network, and Part 1 profile nodes.
    Profile stages share the consuming agent's configuration and BI clients."""

    async def gene_symbol(
        self: Any,
        species_code: str,
        gene_id: str,
        semaphore: asyncio.Semaphore | None = None,
    ) -> list[str]:
        """Return symbols for one gene through the profile lookup seam."""
        return await self._gene_symbol(species_code, gene_id, semaphore)

    async def gene_annotation(
        self: Any,
        species_code: str,
        gene_id: str,
        semaphore: asyncio.Semaphore | None = None,
    ) -> dict[str, Any]:
        """Return annotations for one gene through the profile lookup seam."""
        return await self._gene_annotation(species_code, gene_id, semaphore)

    async def _gene_symbol(
        self: Any,
        species_code: str,
        gene_id: str,
        semaphore: asyncio.Semaphore | None = None,
    ) -> list[str]:
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

        async def get_gene_symbol() -> list[str]:
            return await _cached_gene_symbol_lookup(
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
        semaphore: asyncio.Semaphore | None = None,
    ):
        async def get_gene_annotation() -> dict:
            return await _cached_gene_annotation_lookup(
                species_code=species_code,
                gene_id=gene_id,
                timeout=self.deep_genome_config.TIMEOUT,
            )

        if semaphore is not None:
            async with semaphore:
                return await get_gene_annotation()
        else:
            return await get_gene_annotation()
