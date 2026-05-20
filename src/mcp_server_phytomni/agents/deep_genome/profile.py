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
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from httpx import AsyncClient, Timeout
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from ...common.docs import format_retrieved_doc_context
from ...common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
    require_json_object,
)
from ...common.prompts import get_prompt
from ...common.responses import message_content
from ...config.defaults import DeepGenomeConfig
from ...func_cache import LONG_TTL_SECONDS, func_cache
from ...runtime.workflow_mixins import WorkflowMixinBase
from ..chat.service import phyto_chat
from .formatting import SPECIES_CODE_MAP, network_to_string

if TYPE_CHECKING:
    from .agent import DeepGenomeState
else:
    DeepGenomeState = Dict[str, Any]

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
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        try:
            data = await post_json_with_retries(
                client,
                JsonPostRequest(
                    url=bi_url,
                    headers=sql_headers,
                    json_body={"sql": sql, "returnType": "json"},
                ),
                JsonPostRetry(
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
        "SELECT * FROM id_table WHERE gene_id = "
        f"'{gene_id}' AND species_code = '{species_code}'"
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
    sql_list = (
        "SELECT description FROM annotation_gene_description "
        f"WHERE gene_id = '{gene_id}' "
        f"AND species_code = '{species_code}'",
        "SELECT go_id, go_name FROM annotation_gene_ontology WHERE "
        f"gene_id = '{gene_id}' "
        f"AND species_code = '{species_code}'",
        "SELECT interpro_id, interpro_name "
        "FROM annotation_gene_interpro "
        f"WHERE gene_id = '{gene_id}' "
        f"AND species_code = '{species_code}'",
        "SELECT mapman, mapman_description "
        "FROM annotation_gene_mapman "
        f"WHERE gene_id = '{gene_id}' "
        f"AND species_code = '{species_code}'",
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

    async def _run_orthologs_node(self: Any, state: DeepGenomeState):
        """Retrieve orthologous gene symbols for network analysis.

        This node fetches gene symbols for orthologous genes in parallel using
        a semaphore to limit concurrency.

        Args:
            state: Current workflow state containing orthologs_data.

        Returns:
            Dict with updated orthologs_data including gene_symbol mapping.
        """
        print("=> Retrieving orthologous genes...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        orthologs_species_gene_list = state["orthologs_data"]["gene_list"]
        orthologs_symbol_tasks = [
            self._gene_symbol(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in orthologs_species_gene_list
        ]
        orthologs_gene_symbol_results = await asyncio.gather(
            *(orthologs_symbol_tasks), return_exceptions=True
        )
        species_orthologs_gene_symbol_dict = {
            species_gene: res
            for species_gene, res in zip(
                orthologs_species_gene_list, orthologs_gene_symbol_results
            )
            if res and not isinstance(res, Exception)
        }
        print("Orthologous genes retrieval completed <=")
        return {
            "orthologs_data": {
                "gene_list": orthologs_species_gene_list,
                "gene_symbol": species_orthologs_gene_symbol_dict,
            }
        }

    async def _run_paralogs_node(self: Any, state: DeepGenomeState):
        """Retrieve paralogous gene symbols for network analysis.

        This node fetches gene symbols for paralogous genes in parallel using
        a semaphore.

        Args:
            state: Current workflow state containing paralogs_data.

        Returns:
            Dict with updated paralogs_data including gene_symbol mapping.
        """
        print("=> Retrieving paralogous genes...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        paralogs_species_gene_list = state["paralogs_data"]["gene_list"]
        paralogs_symbol_tasks = [
            self._gene_symbol(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in paralogs_species_gene_list
        ]
        paralogs_gene_symbol_results = await asyncio.gather(
            *(paralogs_symbol_tasks), return_exceptions=True
        )
        species_paralogs_gene_symbol_dict = {
            species_gene: res
            for species_gene, res in zip(
                paralogs_species_gene_list, paralogs_gene_symbol_results
            )
            if res and not isinstance(res, Exception)
        }
        print("Paralogous genes retrieval completed <=")
        return {
            "paralogs_data": {
                "gene_list": paralogs_species_gene_list,
                "gene_symbol": species_paralogs_gene_symbol_dict,
            }
        }

    async def _run_interaction_node(self: Any, state: DeepGenomeState):
        """Retrieve interacting gene symbols for network analysis.

        This node fetches gene symbols for protein interaction partners in
        parallel using a semaphore to limit concurrency.

        Args:
            state: Current workflow state containing interaction_data.

        Returns:
            Dict with updated interaction_data including gene_symbol mapping.
        """
        print("=> Retrieving interacting genes...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        interaction_species_gene_list = state["interaction_data"]["gene_list"]
        interaction_genes = interaction_species_gene_list
        interaction_symbol_tasks = [
            self._gene_symbol(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in interaction_genes
        ]
        interaction_gene_symbol_results = await asyncio.gather(
            *(interaction_symbol_tasks), return_exceptions=True
        )
        species_interaction_gene_symbol_dict = {
            species_gene: res
            for species_gene, res in zip(
                interaction_species_gene_list, interaction_gene_symbol_results
            )
            if res and not isinstance(res, Exception)
        }
        print("Interacting genes retrieval completed <=")
        return {
            "interaction_data": {
                "gene_list": interaction_species_gene_list,
                "gene_symbol": species_interaction_gene_symbol_dict,
            }
        }

    async def _run_orthologs_annotation_node(
        self: Any, state: DeepGenomeState
    ):
        """Summarize orthologous gene network.

        This node fetches annotations for all orthologous genes and generates
        a formatted string summary using the network_to_string function.

        Args:
            state: Current workflow state containing orthologs_data.

        Returns:
            Dict with orthologs_summary and part1_completed_branches increment.
        """
        print("=> Summarizing orthologous gene network...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        orthologs_species_gene_list = state["orthologs_data"]["gene_list"]
        species_orthologs_gene_symbol_dict = state["orthologs_data"].get(
            "gene_symbol", {}
        )

        # Fetch annotations for all orthologs genes
        orthologs_anno_tasks = [
            self._gene_annotation(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in orthologs_species_gene_list
        ]
        orthologs_gene_anno_results = await asyncio.gather(
            *(orthologs_anno_tasks), return_exceptions=True
        )
        species_orthologs_gene_anno_dict = {
            species_gene: res
            for species_gene, res in zip(
                orthologs_species_gene_list, orthologs_gene_anno_results
            )
            if res and not isinstance(res, Exception)
        }

        orthologs_string = network_to_string(
            orthologs_species_gene_list,
            species_orthologs_gene_symbol_dict,
            species_orthologs_gene_anno_dict,
            "Orthologous",
        )
        print("Orthologous gene network summary completed <=")
        return {
            "orthologs_summary": orthologs_string,
            "part1_completed_branches": 1,
        }

    async def _run_paralogs_annotation_node(self: Any, state: DeepGenomeState):
        """Summarize paralogous gene network.

        This node fetches annotations for all paralogous genes and generates
        a formatted string summary using the network_to_string function.

        Args:
            state: Current workflow state containing paralogs_data.

        Returns:
            Dict with paralogs_summary and part1_completed_branches increment.
        """
        print("=> Summarizing paralogous gene network...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        paralogs_species_gene_list = state["paralogs_data"]["gene_list"]
        species_paralogs_gene_symbol_dict = state["paralogs_data"].get(
            "gene_symbol", {}
        )

        # Fetch annotations for all paralogs genes
        paralogs_anno_tasks = [
            self._gene_annotation(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in paralogs_species_gene_list
        ]
        paralogs_gene_anno_results = await asyncio.gather(
            *(paralogs_anno_tasks), return_exceptions=True
        )
        species_paralogs_gene_anno_dict = {
            species_gene: res
            for species_gene, res in zip(
                paralogs_species_gene_list, paralogs_gene_anno_results
            )
            if res and not isinstance(res, Exception)
        }

        paralogs_string = network_to_string(
            paralogs_species_gene_list,
            species_paralogs_gene_symbol_dict,
            species_paralogs_gene_anno_dict,
            "Paralogous",
        )
        print("Paralogous gene network summary completed <=")
        return {
            "paralogs_summary": paralogs_string,
            "part1_completed_branches": 1,
        }

    async def _run_interaction_annotation_node(
        self: Any, state: DeepGenomeState
    ):
        """Summarize interacting gene network.

        This node fetches annotations for all interacting genes and generates
        a formatted string summary using the network_to_string function.

        Args:
            state: Current workflow state containing interaction_data.

        Returns:
            Dict with interaction_summary and part1 branch increment.
        """
        print("=> Summarizing interacting gene network...")
        semaphore = asyncio.Semaphore(self.deep_genome_config.MAX_CONCURRENCY)
        interaction_species_gene_list = state["interaction_data"]["gene_list"]
        interaction_genes = interaction_species_gene_list
        species_interaction_gene_symbol_dict = state["interaction_data"].get(
            "gene_symbol", {}
        )

        # Fetch annotations for all interaction genes
        interaction_anno_tasks = [
            self._gene_annotation(
                species_code=each_species_code,
                gene_id=each_gene_id,
                semaphore=semaphore,
            )
            for each_species_code, each_gene_id in interaction_genes
        ]
        interaction_gene_anno_results = await asyncio.gather(
            *(interaction_anno_tasks), return_exceptions=True
        )
        species_interaction_gene_anno_dict = {
            species_gene: res
            for species_gene, res in zip(
                interaction_species_gene_list, interaction_gene_anno_results
            )
            if res and not isinstance(res, Exception)
        }

        interaction_string = network_to_string(
            interaction_species_gene_list,
            species_interaction_gene_symbol_dict,
            species_interaction_gene_anno_dict,
            "Potential interacting",
        )
        print("Interacting gene network summary completed <=")
        return {
            "interaction_summary": interaction_string,
            "part1_completed_branches": 1,
        }

    async def _run_part1_node(self: Any, state: DeepGenomeState):
        """Barrier node for Part 1 - Gene Network Profile aggregation.

        This node waits for 4 parallel branches to complete, then generates
        an integrated gene function network report using LLM.

        Args:
            state: Current workflow state containing all network data.

        Returns:
            Dict with part1_report and experiment_completed_branches increment,
            or empty dict if barrier not yet satisfied.
        """
        # Barrier 1: Wait for 4 branches to complete
        print(
            "part1_completed_branches: ",
            state.get("part1_completed_branches", 0),
        )
        if state.get("part1_completed_branches", 0) < 4:
            return {}

        print(
            "\n[Merging] Part 1 Gene Network Profile data ready, "
            "generating integrated report..."
        )

        user_query = get_prompt(
            self.deep_genome_config.PROMPT_FILE,
            "user/gene_function_network_anno",
            self._part1_prompt_vars(state),
        )

        phyto_response = await phyto_chat(
            user_query=user_query,
            **self._chat_kwargs(),
        )

        part1_report = message_content(phyto_response)
        if not part1_report:
            part1_report = (
                f"Gene {state['gene_id']} basic profile generation failed"
            )

        return {
            "part1_report": part1_report,
            "experiment_completed_branches": 1,
        }

    def _part1_prompt_vars(
        self: Any, state: DeepGenomeState
    ) -> Dict[str, Any]:
        """Build prompt variables for the integrated Part 1 report."""
        gene_annotation = state.get("gene_annotation", {})
        retrieve_context, _ = format_retrieved_doc_context(
            state.get("knowledge_context", {}).get("literature", ""),
            self.deep_genome_config.MAX_TOKENS,
        )
        return {
            "species": SPECIES_CODE_MAP[state["species_code"]],
            "gene_string": gene_annotation.get("gene_string", ""),
            "retrieve_results": retrieve_context,
            "description_string": gene_annotation.get("description", ""),
            "go_string": gene_annotation.get("go", ""),
            "interpro_string": gene_annotation.get("interpro", ""),
            "mapman_string": gene_annotation.get("mapman", ""),
            "orthologs_string": state.get("orthologs_summary", ""),
            "paralogs_string": state.get("paralogs_summary", ""),
            "interaction_string": state.get("interaction_summary", ""),
        }
