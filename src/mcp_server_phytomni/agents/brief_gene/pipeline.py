# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Brief gene pipeline helpers: BI API, retrieval, and annotation formatting.

Exports the data transformation helpers, BI API wrappers, literature
retrieval functions, and annotation formatters used by BriefGeneAgent
nodes. These are the reusable pieces that aren't tied to the agent class
itself. The agent orchestration lives in agent.py; HTTP-only query
resolution lives in resolve_query.py.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...common.http import JsonPostRetry, require_json_object
from ...common.prompts import get_prompt
from ...common.responses import (
    attach_message_payload,
    message_content,
    parse_follow_up_questions,
)
from ...config.defaults import BriefGeneConfig
from ..chat.service import phyto_chat
from ..knowledge.agent import KnowledgeAgent
from ..knowledge.retrieval_result import retrieval_unavailable_error
from ..shared.sql import bi_query

BRIEF_CONFIG = BriefGeneConfig()


@dataclass(frozen=True)
class GeneRetrieveRequest:
    """Stable request shape for one gene literature retrieval call.

    The composite-level cache that previously keyed on this dataclass
    has been removed; the actual caching now lives one layer down in
    the knowledge retrieval HTTP primitives, so this struct is just a
    convenient bundle for the public ``gene_retrieve`` wrapper.

    Attributes:
        species: Species display name included in retrieval queries.
        symbols: Deduplicated gene symbols and identifiers to retrieve.
        top_n: Maximum number of retrieved documents to retain.
    """

    species: str
    symbols: tuple[str, ...]
    top_n: int


def _response_data(response: Any) -> list[dict[str, Any]]:
    """Return BI API data rows from a response if present."""
    if not isinstance(response, dict) or response.get("message") != "ok":
        return []
    data = response.get("data", [])
    return data if isinstance(data, list) else []


def _first_row(response: Any) -> dict[str, Any] | None:
    """Return the first BI API row, if available."""
    data = _response_data(response)
    return data[0] if data and isinstance(data[0], dict) else None


def _split_symbols(symbols: str) -> list[str]:
    """Split pipe-separated gene symbols into a clean list."""
    if not symbols:
        return []
    return [symbol.strip() for symbol in symbols.split("|") if symbol.strip()]


def _dedupe(values: list[str]) -> list[str]:
    """Preserve order while removing empty strings and duplicates."""
    seen = set()
    deduped = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _doc_content(doc: dict[str, Any]) -> str:
    """Return the best available document text field."""
    return str(doc.get("big_content") or doc.get("content") or "")


def _format_docs(doc_list: list[dict[str, Any]], max_tokens: int) -> str:
    """Format retrieved documents into prompt context."""
    fragments = []
    total_length = 0
    for i, doc in enumerate(doc_list):
        header = f"[document {i + 1} begin] {doc.get('title', '')}"
        subtitle = doc.get("subtitle", "")
        content = _doc_content(doc)
        body = f"{subtitle}\n{content}" if subtitle else content
        fragment = f"{header}\n{body} [document {i + 1} end]"
        if total_length + len(fragment) <= max_tokens:
            fragments.append(fragment)
            total_length += len(fragment)
        else:
            break
    return "\n\n".join(fragments)


def _attach_metadata(
    phyto_response: dict[str, Any],
    doc_list: list[dict[str, Any]],
    follow_up_questions: list[str] | None = None,
) -> dict[str, Any]:
    """Attach references and follow-up questions to a model response."""
    payload: dict[str, Any] = {"doc_list": doc_list, "total": 10000}
    if follow_up_questions is not None:
        payload["follow_up_questions"] = follow_up_questions
    return attach_message_payload(phyto_response, payload)


def _annotation_rows(response: Any) -> list[dict[str, Any]]:
    """Validate one successful BI annotation response and detach its rows."""
    if not isinstance(response, Mapping) or response.get("message") != "ok":
        raise ValueError("invalid BI annotation response")
    data = response.get("data")
    if not isinstance(data, list):
        raise ValueError("invalid BI annotation response")
    if any(not isinstance(row, Mapping) for row in data):
        raise ValueError("invalid BI annotation response")
    return [dict(row) for row in data]


def _partition_annotation_results(
    results: list[Any],
) -> tuple[list[list[dict[str, Any]]], list[int]]:
    """Partition valid BI rows from bounded ordinary table failures.

    A successful ``message=ok`` response with ``data=[]`` is preserved as a
    valid absence. Exception text and response bodies never leave this helper;
    only the failed table ordinal is returned for later evidence decisions.
    Cancellation and other ``BaseException`` values are re-raised unchanged.
    """
    rows_by_index: list[list[dict[str, Any]]] = []
    failed_indices: list[int] = []
    for index, result in enumerate(results):
        if isinstance(result, BaseException) and not isinstance(
            result, Exception
        ):
            raise result
        if isinstance(result, Exception):
            rows_by_index.append([])
            failed_indices.append(index)
            continue
        try:
            rows_by_index.append(_annotation_rows(result))
        except (TypeError, ValueError):
            rows_by_index.append([])
            failed_indices.append(index)
    return rows_by_index, failed_indices


def _go_annotation_string(go_rows: list[dict[str, Any]]) -> str:
    """Format selected GO annotations."""
    core_rows = [
        row
        for row in go_rows
        if str(row.get("is_propagated_from_child_term")) == "0"
    ] or [
        row
        for row in go_rows
        if str(row.get("is_propagated_from_child_term")) == "1"
    ]
    return (
        " ; ".join(
            _dedupe(
                [
                    f"{row.get('go_id')} ({row.get('go_name')})"
                    for row in core_rows
                    if row.get("go_id") or row.get("go_name")
                ]
            )
        )
        or "No annotation available."
    )


def _mapman_annotation_string(mapman_rows: list[dict[str, Any]]) -> str:
    """Format MapMan descriptions while skipping uninformative entries."""
    invalid_keywords = ["not assigned", "unknown", "not annotate"]
    return (
        " ; ".join(
            _dedupe(
                [
                    str(row.get("mapman_description", "")).strip()
                    for row in mapman_rows
                    if row.get("mapman_description")
                    and not any(
                        keyword
                        in str(row.get("mapman_description", "")).lower()
                        for keyword in invalid_keywords
                    )
                ]
            )
        )
        or "No annotation available."
    )


def _interpro_annotation_string(interpro_rows: list[dict[str, Any]]) -> str:
    """Format InterPro annotation names."""
    return (
        " ; ".join(
            _dedupe(
                [
                    str(row.get("interpro_name", "")).strip()
                    for row in interpro_rows
                    if row.get("interpro_name")
                ]
            )
        )
        or "No annotation available."
    )


def _annotation_strings_delta(
    annotation_rows: list[list[dict[str, Any]]],
    structure_row: dict[str, Any],
) -> dict[str, str]:
    """Format the five annotation flat strings into a state-delta dict.

    Keeps ``fetch_annotation_node`` within pylint R0914 too-many-locals
    cap by computing the five derived strings (gene_structure /
    go / kegg / interpro / description) in one place and returning
    them as a dict the caller spreads into its state delta.
    """
    return {
        "gene_structure_string": _gene_structure_annotation_string(
            structure_row
        ),
        "go_string": _go_annotation_string(annotation_rows[2]),
        "kegg_string": _mapman_annotation_string(annotation_rows[3]),
        "interpro_string": _interpro_annotation_string(annotation_rows[4]),
        "description_string": _description_annotation_string(
            annotation_rows[5]
        ),
    }


def _alias_counts_delta(
    gene_id_info_response: dict[str, Any] | None,
) -> dict[str, int]:
    """Derive Basic Information cross-species alias counts.

    The ``id2multispecies`` response when queried with the resolved
    canonical gene_id returns one row per cross-species mapping;
    counting the rows gives the alias count and counting distinct
    ``species_code`` values gives the species-count summary.
    Returns the dict pair directly so ``query_judge_node`` can
    spread it into its state delta without allocating intermediate
    locals (keeps pylint R0914 too-many-locals happy).
    """
    rows = (gene_id_info_response or {}).get("data") or []
    species = {
        str(item.get("species_code", "")).strip()
        for item in rows
        if item.get("species_code")
    }
    return {
        "cross_species_alias_count": len(rows),
        "cross_species_alias_species_count": len(species),
    }


def _gene_structure_annotation_string(structure_row: dict[str, Any]) -> str:
    """Build a concise display string for gene structural metadata.

    Excludes location columns (``chromosome``, ``start``, ``end``,
    ``strand``) already projected to separate state fields and
    indexing columns (``gene_id``, ``species_code``,
    ``sequence_type``); concatenates remaining non-empty key/value
    pairs as ``key: value`` separated by ``; ``. Empty input
    returns an empty string so downstream prompts can render
    "Gene Structure: " gracefully even when BI lacks structural
    annotation for the gene.
    """
    if not structure_row:
        return ""
    skip = {
        "chromosome",
        "start",
        "end",
        "strand",
        "gene_id",
        "species_code",
        "sequence_type",
    }
    parts: list[str] = []
    for key, value in structure_row.items():
        if key in skip:
            continue
        if value in (None, "", 0, "0"):
            continue
        parts.append(f"{key}: {value}")
    return "; ".join(parts)


def _description_annotation_string(
    description_rows: list[dict[str, Any]],
) -> str:
    """Format gene description text from BI annotation rows.

    Mirrors the ``descruption_string`` (sic, deep_genome's spelling)
    projection in
    ``deep_genome/dispatch.py:_run_gene_annotation_node``: joins the
    per-row ``description`` field with ``; `` separators so a
    consumer agent mounting brief_gene as a subgraph reads the same
    description shape it previously computed inline. Empty / null
    rows are dropped; a fully empty result returns the legacy
    ``"No annotation available."`` sentinel so downstream prompts
    never receive a bare empty string.
    """
    return (
        "; ".join(
            _dedupe(
                [
                    str(row.get("description", "")).strip()
                    for row in description_rows
                    if row.get("description")
                ]
            )
        )
        or "No annotation available."
    )


async def run_bi_api(
    query_sql: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Invoke the BI API to retrieve annotation information.

    Args:
        query_sql: SQL statement sent to the BI endpoint.
        **kwargs: Optional timeout, retriable_codes, and max_retries
            overrides.

    Returns:
        BI API JSON payload.

    Raises:
        McpError: If the BI API request fails after all retries.
    """
    timeout = kwargs.get("timeout", BRIEF_CONFIG.TIMEOUT)
    retriable_codes = kwargs.get("retriable_codes")
    max_retries = kwargs.get("max_retries", BRIEF_CONFIG.MAX_RETRIES)
    if retriable_codes is None:
        retriable_codes = list(BRIEF_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    data = await bi_query(
        query_sql,
        retry=JsonPostRetry(
            timeout=timeout,
            max_retries=max_retries,
            retriable_codes=retriable_codes,
            message="Failed to query BI API",
            network_message="BI API network error",
        ),
    )
    return require_json_object(
        data, "Failed to query BI API after all retries"
    )


async def gene_retrieve(
    species: str,
    gene_symbol_list: list[str],
    knowledge_agent: KnowledgeAgent,
    **kwargs: Any,
) -> dict[str, Any]:
    """Retrieve literature for a gene through the LangGraph KnowledgeAgent.

    Args:
        species: Species display name used to qualify retrieval queries.
        gene_symbol_list: Candidate gene symbols and identifiers.
        knowledge_agent: KnowledgeAgent used for retrieval.
        **kwargs: Optional top_n and semaphore overrides.

    Returns:
        Retrieval payload containing ``doc_list`` and ``total``.
    """
    top_n = kwargs.get("top_n", BRIEF_CONFIG.TOP_N)
    semaphore = kwargs.get("semaphore")
    symbols = tuple(_dedupe(gene_symbol_list))
    if not symbols:
        return {"doc_list": [], "total": 10000}

    request = GeneRetrieveRequest(
        species=species,
        symbols=symbols,
        top_n=top_n,
    )
    return await _gene_retrieve(
        request=request,
        knowledge_agent=knowledge_agent,
        semaphore=semaphore,
    )


async def _gene_retrieve(
    request: GeneRetrieveRequest,
    knowledge_agent: KnowledgeAgent,
    semaphore: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Fan out one KnowledgeAgent.arun per symbol and merge the docs.

    The composite cache that previously sat on this function is gone;
    de-duplication of repeated retrieval roundtrips now happens inside
    the knowledge retrieval HTTP primitive caches, which are keyed on
    the actual semantic inputs rather than this layer's bundled
    request object.
    """
    combined_symbols = "\n".join(request.symbols)
    query_terms = _dedupe([*request.symbols, combined_symbols])

    async def make_gene_retrieve() -> dict[str, Any]:
        tasks = [
            knowledge_agent.arun(
                user_query=f"{request.species}\n{symbol}",
                is_generate=False,
                is_follow_up=False,
            )
            for symbol in query_terms
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged_docs: list[dict[str, Any]] = []
        failed = False
        for result in results:
            if isinstance(result, BaseException) and not isinstance(
                result, Exception
            ):
                raise result
            if isinstance(result, Exception):
                failed = True
                continue
            if not isinstance(result, list) or any(
                not isinstance(doc, dict) for doc in result
            ):
                failed = True
                continue
            merged_docs.extend(result)
        if failed and not merged_docs:
            raise retrieval_unavailable_error()
        sorted_docs = sorted(
            merged_docs, key=lambda item: item.get("score", 0), reverse=True
        )
        if request.top_n is not None and request.top_n > 0:
            sorted_docs = sorted_docs[: request.top_n]
        return {"doc_list": sorted_docs, "total": 10000}

    if semaphore is not None:
        async with semaphore:
            return await make_gene_retrieve()
    return await make_gene_retrieve()


async def _generate_follow_up(
    user_query: str,
    phyto_response: dict[str, Any],
    **kwargs: Any,
) -> list[str]:
    """Generate follow-up questions for a brief gene response."""
    prompt_file = kwargs.get("prompt_file", BRIEF_CONFIG.PROMPT_FILE)
    follow_up_response = await phyto_chat(
        user_query=get_prompt(
            prompt_file,
            "system/follow_up_questions",
            {
                "user_query": f"What is gene function of {user_query}",
                "system_response": message_content(phyto_response),
            },
        ),
        **kwargs,
    )
    return parse_follow_up_questions(message_content(follow_up_response))
