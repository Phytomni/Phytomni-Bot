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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from httpx import Timeout

from ...common.http import (
    JsonPostRequest,
    JsonPostRetry,
    post_json_with_retries,
    require_json_object,
)
from ...common.httpx_client import get_async_client
from ...common.prompts import get_prompt
from ...common.responses import (
    attach_message_payload,
    message_content,
    parse_follow_up_questions,
)
from ...config.defaults import BriefGeneConfig
from ...config.settings import get_sensitive_config
from ..chat.service import phyto_chat
from ..knowledge.agent import KnowledgeAgent
from ..knowledge.retrieval import clear_retrieval_caches

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


def _response_data(response: Any) -> List[Dict[str, Any]]:
    """Return BI API data rows from a response if present."""
    if not isinstance(response, dict) or response.get("message") != "ok":
        return []
    data = response.get("data", [])
    return data if isinstance(data, list) else []


def _first_row(response: Any) -> Optional[Dict[str, Any]]:
    """Return the first BI API row, if available."""
    data = _response_data(response)
    return data[0] if data and isinstance(data[0], dict) else None


def _split_symbols(symbols: str) -> List[str]:
    """Split pipe-separated gene symbols into a clean list."""
    if not symbols:
        return []
    return [symbol.strip() for symbol in symbols.split("|") if symbol.strip()]


def _dedupe(values: List[str]) -> List[str]:
    """Preserve order while removing empty strings and duplicates."""
    seen = set()
    deduped = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _doc_content(doc: Dict[str, Any]) -> str:
    """Return the best available document text field."""
    return str(doc.get("big_content") or doc.get("content") or "")


def _format_docs(doc_list: List[Dict[str, Any]], max_tokens: int) -> str:
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
    phyto_response: Dict[str, Any],
    doc_list: List[Dict[str, Any]],
    follow_up_questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Attach references and follow-up questions to a model response."""
    payload: Dict[str, Any] = {"doc_list": doc_list, "total": 10000}
    if follow_up_questions is not None:
        payload["follow_up_questions"] = follow_up_questions
    return attach_message_payload(phyto_response, payload)


def _safe_rows(results: List[Any], index: int) -> List[Dict[str, Any]]:
    """Return BI response rows for one gather result index."""
    result = results[index]
    if isinstance(result, Exception):
        return []
    return _response_data(result)


def _go_annotation_string(go_rows: List[Dict[str, Any]]) -> str:
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


def _mapman_annotation_string(mapman_rows: List[Dict[str, Any]]) -> str:
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


def _interpro_annotation_string(interpro_rows: List[Dict[str, Any]]) -> str:
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


async def run_bi_api(
    query_sql: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Invoke the BI API to retrieve annotation information.

    Args:
        query_sql: SQL statement sent to the BI endpoint.
        **kwargs: Optional bi_url, bi_token, timeout, retriable_codes, and
            max_retries overrides.

    Returns:
        BI API JSON payload.

    Raises:
        McpError: If the BI API request fails after all retries.
    """
    bi_url = kwargs.get("bi_url", BRIEF_CONFIG.BI_URL)
    bi_token = kwargs.get(
        "bi_token", get_sensitive_config().BI_TOKEN.get_secret_value()
    )
    timeout = kwargs.get("timeout", BRIEF_CONFIG.TIMEOUT)
    retriable_codes = kwargs.get("retriable_codes")
    max_retries = kwargs.get("max_retries", BRIEF_CONFIG.MAX_RETRIES)
    if retriable_codes is None:
        retriable_codes = list(BRIEF_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    client_timeout = Timeout(timeout, connect=timeout)
    async with get_async_client(timeout=client_timeout) as client:
        data = await post_json_with_retries(
            client,
            JsonPostRequest(
                url=bi_url,
                headers={
                    "Content-Type": "application/json",
                    "token": bi_token,
                },
                json_body={"sql": query_sql, "returnType": "json"},
            ),
            JsonPostRetry(
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
    gene_symbol_list: List[str],
    knowledge_agent: KnowledgeAgent,
    **kwargs: Any,
) -> Dict[str, Any]:
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
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Fan out one KnowledgeAgent.arun per symbol and merge the docs.

    The composite cache that previously sat on this function is gone;
    de-duplication of repeated retrieval roundtrips now happens inside
    the knowledge retrieval HTTP primitive caches, which are keyed on
    the actual semantic inputs rather than this layer's bundled
    request object. Call ``clear_gene_retrieve_cache()`` (the shim) to
    drop the underlying retrieval-primitive state when testing or
    administering the cache.
    """
    combined_symbols = "\n".join(request.symbols)
    query_terms = _dedupe([*request.symbols, combined_symbols])

    async def make_gene_retrieve() -> Dict[str, Any]:
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
        for result in results:
            if isinstance(result, dict):
                docs = result.get("doc_list", [])
            elif isinstance(result, list):
                docs = result
            else:
                continue
            merged_docs.extend(doc for doc in docs if isinstance(doc, dict))
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


def clear_gene_retrieve_cache() -> None:
    """Compatibility shim that drops the retrieval primitive caches.

    Kept under the original name so existing importers and tests
    continue to work. Internally it delegates to the knowledge-layer
    ``clear_retrieval_caches`` since brief_gene no longer owns a
    composite cache.
    """
    clear_retrieval_caches()


async def _generate_follow_up(
    user_query: str,
    phyto_response: Dict[str, Any],
    **kwargs: Any,
) -> List[str]:
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
