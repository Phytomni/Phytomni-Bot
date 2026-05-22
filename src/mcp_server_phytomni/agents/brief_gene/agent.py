# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Brief gene function summaries from BI annotations and literature RAG.

Exports BI query helpers, cached literature retrieval, BriefGeneAgent state
and graph nodes, cache clearing utilities, and the brief_gene_function wrapper
used by MCP handlers.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict

from httpx import Timeout
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

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
from ...config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from ...config.settings import SensitiveConfig, get_sensitive_config
from ...runtime.agent_registry import (
    agent_fingerprint_values,
    get_cached_agent,
)
from ...runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from ..chat.service import phyto_chat
from ..knowledge.agent import KnowledgeAgent
from ..knowledge.retrieval import clear_retrieval_caches
from ..shared.sql import sql_literal

BRIEF_CONFIG = BriefGeneConfig()

BRIEF_GENE_CONFIG_FIELD_MAP = {
    **CHAT_COMPLETION_CONFIG_FIELD_MAP,
    **RETRIEVAL_CONFIG_FIELD_MAP,
    **RETRY_CONFIG_FIELD_MAP,
    "bi_url": "BI_URL",
    "max_concurrency": "MAX_CONCURRENCY",
}
BRIEF_GENE_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
BRIEF_GENE_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
    "bi_token": "BI_TOKEN",
}


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


class BriefGeneAgentState(TypedDict):
    """State schema for the brief gene LangGraph workflow.

    Attributes:
        user_query: Original gene identifier or free-text query.
        gene_found: Whether the BI id table resolved the query.
        gene_id: Canonical resolved gene id.
        query_id_version: Identifier type for the original query.
        gene_id_version: Identifier type for the canonical gene id.
        species_code: Resolved species code.
        species_latin_name: Resolved Latin species name.
        species_english_name: Resolved English species name.
        species_all_name: Combined display species string.
        gene_name_symbol_list: Symbols from the BI id table.
        gene_id_list: Deduplicated gene ids and symbols for retrieval.
        gene_chr: Chromosome from structure annotation.
        gene_start: Start coordinate from structure annotation.
        gene_end: End coordinate from structure annotation.
        gene_strand: Strand from structure annotation.
        go_string: Formatted GO annotation summary.
        kegg_string: Formatted MapMan/KEGG-like annotation summary.
        interpro_string: Formatted InterPro annotation summary.
        retrieved_docs: Documents retrieved from the knowledge agent.
        retrieve_context: Prompt-ready retrieved document context.
        follow_up_questions: Suggested follow-up questions.
        final_response: Chat-completions-style final response payload.
    """

    user_query: str
    gene_found: bool
    gene_id: str
    query_id_version: str
    gene_id_version: str
    species_code: str
    species_latin_name: str
    species_english_name: str
    species_all_name: str
    gene_name_symbol_list: List[str]
    gene_id_list: List[str]
    gene_chr: str
    gene_start: str
    gene_end: str
    gene_strand: str
    go_string: str
    kegg_string: str
    interpro_string: str
    retrieved_docs: List[Dict[str, Any]]
    retrieve_context: str
    follow_up_questions: List[str]
    final_response: Dict[str, Any]


class BriefGeneAgent:
    """LangGraph-based agent for brief gene function analysis.

    Attributes:
        brief_config: Public config for BI, retrieval, and chat defaults.
        sensitive_config: Sensitive config with model and BI credentials.
        ka: KnowledgeAgent used for literature retrieval.
        checkpointer: LangGraph checkpointer used by the compiled graph.
        app: Compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        brief_config: BriefGeneConfig = BRIEF_CONFIG,
        sensitive_config: Optional[SensitiveConfig] = None,
        knowledge_agent: Optional[KnowledgeAgent] = None,
    ):
        self.brief_config = brief_config
        self.sensitive_config = sensitive_config or get_sensitive_config()
        self.ka = knowledge_agent or KnowledgeAgent(
            knowledge_config=brief_config,
            sensitive_config=self.sensitive_config,
        )
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.app = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(BriefGeneAgentState)
        workflow.add_node("query_judge_node", self.query_judge_node)
        workflow.add_node("fetch_annotation_node", self.fetch_annotation_node)
        workflow.add_node("retrieve_node", self.retrieve_node)
        workflow.add_node("generate_node", self.generate_node)
        workflow.add_node("follow_up_node", self.follow_up_node)

        workflow.add_edge(START, "query_judge_node")
        workflow.add_conditional_edges(
            "query_judge_node", self.route_after_judge
        )
        workflow.add_edge("fetch_annotation_node", "retrieve_node")
        workflow.add_edge("retrieve_node", "generate_node")
        workflow.add_edge("generate_node", "follow_up_node")
        workflow.add_edge("follow_up_node", END)
        return workflow.compile(checkpointer=self.checkpointer)

    def route_after_judge(self, state: BriefGeneAgentState) -> str:
        """Route to annotation lookup only when BI found the gene.

        Args:
            state: Current workflow state after query judging.

        Returns:
            Next node name for annotation lookup or direct retrieval.
        """
        if state["gene_found"]:
            return "fetch_annotation_node"
        return "retrieve_node"

    async def query_judge_node(self, state: BriefGeneAgentState):
        """Check whether the query is known to the BI gene ID table.

        Args:
            state: Current workflow state containing the user query.

        Returns:
            State updates containing gene resolution and species metadata, or
            ``gene_found=False`` when BI has no match.
        """
        user_query = state["user_query"]
        query_response = await run_bi_api(
            "SELECT * FROM id2multispecies "
            f"WHERE query_id = {sql_literal(user_query)}",
            bi_url=self.brief_config.BI_URL,
            bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
            timeout=self.brief_config.TIMEOUT,
            retriable_codes=self.brief_config.RETRIABLE_CODES,
            max_retries=self.brief_config.MAX_RETRIES,
        )
        row = _first_row(query_response)
        if row is None:
            return {"gene_found": False}

        gene_id = str(row.get("gene_id", ""))
        species_code = str(row.get("species_code", ""))
        gene_id_info_response, species_response = await asyncio.gather(
            run_bi_api(
                "SELECT * FROM id2multispecies "
                f"WHERE query_id = {sql_literal(gene_id)}",
                bi_url=self.brief_config.BI_URL,
                bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
                timeout=self.brief_config.TIMEOUT,
                retriable_codes=self.brief_config.RETRIABLE_CODES,
                max_retries=self.brief_config.MAX_RETRIES,
            ),
            run_bi_api(
                "SELECT * FROM species "
                f"WHERE species_code = {sql_literal(species_code)}",
                bi_url=self.brief_config.BI_URL,
                bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
                timeout=self.brief_config.TIMEOUT,
                retriable_codes=self.brief_config.RETRIABLE_CODES,
                max_retries=self.brief_config.MAX_RETRIES,
            ),
        )
        gene_id_row = _first_row(gene_id_info_response) or {}
        species_row = _first_row(species_response) or {}
        species_latin_name = str(
            species_row.get("species_scientific_name", "")
        )
        species_english_name = str(species_row.get("species_name_eng", ""))
        species_all_name = (
            f"{species_english_name} ({species_latin_name}, {species_code})"
        )
        return {
            "gene_found": True,
            "gene_id": gene_id,
            "query_id_version": str(row.get("id_type", "")),
            "gene_id_version": str(gene_id_row.get("id_type", "")),
            "species_code": species_code,
            "species_latin_name": species_latin_name,
            "species_english_name": species_english_name,
            "species_all_name": species_all_name,
        }

    async def fetch_annotation_node(self, state: BriefGeneAgentState):
        """Fetch gene annotation from BI database tables.

        Args:
            state: Current workflow state containing the resolved gene id.

        Returns:
            State updates containing symbols, coordinates, and formatted
            annotation strings.
        """
        gene_id_literal = sql_literal(state["gene_id"])
        annotation_sqls = [
            f"SELECT * FROM id_table WHERE gene_id = {gene_id_literal}",
            "SELECT * FROM annotation_gene_structure_col "
            f"WHERE gene_id = {gene_id_literal} AND sequence_type = 'gene'",
            "SELECT * FROM annotation_gene_ontology "
            f"WHERE gene_id = {gene_id_literal} LIMIT 50",
            "SELECT * FROM annotation_gene_mapman "
            f"WHERE gene_id = {gene_id_literal}",
            "SELECT * FROM annotation_gene_interpro "
            f"WHERE gene_id = {gene_id_literal}",
        ]
        annotation_responses = await asyncio.gather(
            *[
                run_bi_api(
                    sql,
                    bi_url=self.brief_config.BI_URL,
                    bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
                    timeout=self.brief_config.TIMEOUT,
                    retriable_codes=self.brief_config.RETRIABLE_CODES,
                    max_retries=self.brief_config.MAX_RETRIES,
                )
                for sql in annotation_sqls
            ],
            return_exceptions=True,
        )

        id_rows = _safe_rows(annotation_responses, 0)
        gene_symbols = _split_symbols(
            str(id_rows[0].get("symbol", "")) if id_rows else ""
        )
        gene_id_list = _dedupe(
            [state["user_query"], state["gene_id"], *gene_symbols]
        )

        structure_rows = _safe_rows(annotation_responses, 1)
        structure_row = structure_rows[0] if structure_rows else {}

        go_string = _go_annotation_string(_safe_rows(annotation_responses, 2))
        kegg_string = _mapman_annotation_string(
            _safe_rows(annotation_responses, 3)
        )
        interpro_string = _interpro_annotation_string(
            _safe_rows(annotation_responses, 4)
        )

        return {
            "gene_name_symbol_list": gene_symbols,
            "gene_id_list": gene_id_list,
            "gene_chr": str(structure_row.get("chromosome", "")),
            "gene_start": str(structure_row.get("start", "")),
            "gene_end": str(structure_row.get("end", "")),
            "gene_strand": str(structure_row.get("strand", "")),
            "go_string": go_string,
            "kegg_string": kegg_string,
            "interpro_string": interpro_string,
        }

    async def retrieve_node(self, state: BriefGeneAgentState):
        """Retrieve gene literature through the LangGraph KnowledgeAgent.

        Args:
            state: Current workflow state with gene resolution metadata.

        Returns:
            State updates containing retrieved documents and prompt context.
        """
        if state["gene_found"]:
            result = await gene_retrieve(
                species=state["species_all_name"],
                gene_symbol_list=state["gene_id_list"],
                knowledge_agent=self.ka,
                top_n=self.brief_config.TOP_N,
                semaphore=asyncio.Semaphore(self.brief_config.MAX_CONCURRENCY),
            )
            doc_list = result.get("doc_list", [])
        else:
            result = await self.ka.arun(
                user_query=state["user_query"],
                is_generate=False,
                is_follow_up=False,
            )
            doc_list = result if isinstance(result, list) else []
        return {
            "retrieved_docs": doc_list,
            "retrieve_context": _format_docs(
                doc_list, self.brief_config.MAX_TOKENS
            ),
        }

    async def generate_node(self, state: BriefGeneAgentState):
        """Generate the brief gene function report.

        Args:
            state: Current workflow state with annotations and retrieval text.

        Returns:
            State update containing the initial final response payload.
        """
        if state["gene_found"]:
            prompt_vars = {
                "user_query": state["user_query"],
                "query_id_type": state["query_id_version"],
                "gene_id": state["gene_id"],
                "gene_id_type": state["gene_id_version"],
                "species": state["species_code"],
                "species_latin_name": state["species_latin_name"],
                "species_english_name": state["species_english_name"],
                "gene_string": "|".join(state["gene_id_list"]),
                "chromosome": state["gene_chr"],
                "start": state["gene_start"],
                "end": state["gene_end"],
                "strand": state["gene_strand"],
                "go_terms": state["go_string"],
                "kegg_annotation": state["kegg_string"],
                "interpro_terms": state["interpro_string"],
                "retrieve_results": state["retrieve_context"],
            }
            chat_query = get_prompt(
                self.brief_config.PROMPT_FILE,
                "user/brief_gene_function",
                prompt_vars,
            )
        else:
            chat_query = get_prompt(
                self.brief_config.PROMPT_FILE,
                "user/brief_gene_function_nogeneid",
                {
                    "user_query": state["user_query"],
                    "retrieve_results": state["retrieve_context"],
                },
            )

        phyto_response = await phyto_chat(
            user_query=chat_query,
            prompt_file=self.brief_config.PROMPT_FILE,
            prompt_path=self.brief_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.brief_config.FREQUENCY_PENALTY,
            n=self.brief_config.N,
            presence_penalty=self.brief_config.PRESENCE_PENALTY,
            reasoning_effort=self.brief_config.REASONING_EFFORT,
            response_format=self.brief_config.RESPONSE_FORMAT,
            stream=self.brief_config.STREAM,
            temperature=self.brief_config.TEMPERATURE,
            top_p=self.brief_config.TOP_P,
            user=self.brief_config.USER,
            timeout=self.brief_config.TIMEOUT,
            retriable_codes=self.brief_config.RETRIABLE_CODES,
            max_retries=self.brief_config.MAX_RETRIES,
        )
        if phyto_response is None:
            phyto_response = {"choices": [{"message": {}}]}
        return {
            "final_response": _attach_metadata(
                phyto_response, state["retrieved_docs"]
            )
        }

    async def follow_up_node(self, state: BriefGeneAgentState):
        """Generate follow-up questions for the final report.

        Args:
            state: Current workflow state with the generated response.

        Returns:
            State updates containing follow-up questions and enriched final
            response metadata.
        """
        follow_up_questions = await _generate_follow_up(
            user_query=state["user_query"],
            phyto_response=state["final_response"],
            prompt_file=self.brief_config.PROMPT_FILE,
            prompt_path=self.brief_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.brief_config.FREQUENCY_PENALTY,
            n=self.brief_config.N,
            presence_penalty=self.brief_config.PRESENCE_PENALTY,
            reasoning_effort=self.brief_config.REASONING_EFFORT,
            response_format=self.brief_config.RESPONSE_FORMAT,
            stream=self.brief_config.STREAM,
            temperature=self.brief_config.TEMPERATURE,
            top_p=self.brief_config.TOP_P,
            user=self.brief_config.USER,
            timeout=self.brief_config.TIMEOUT,
            retriable_codes=self.brief_config.RETRIABLE_CODES,
            max_retries=self.brief_config.MAX_RETRIES,
        )
        final_response = _attach_metadata(
            state["final_response"],
            state["retrieved_docs"],
            follow_up_questions,
        )
        return {
            "follow_up_questions": follow_up_questions,
            "final_response": final_response,
        }

    async def arun(
        self, user_query: str, thread_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute the BriefGeneAgent workflow.

        Args:
            user_query: Gene identifier, symbol, or free-text query.
            thread_id: Optional LangGraph checkpoint thread id.

        Returns:
            Chat-completions-style final response payload with content,
            references, and follow-up questions.
        """
        initial_state: BriefGeneAgentState = {
            "user_query": user_query,
            "gene_found": False,
            "gene_id": "",
            "query_id_version": "",
            "gene_id_version": "",
            "species_code": "",
            "species_latin_name": "",
            "species_english_name": "",
            "species_all_name": "",
            "gene_name_symbol_list": [],
            "gene_id_list": [],
            "gene_chr": "",
            "gene_start": "",
            "gene_end": "",
            "gene_strand": "",
            "go_string": "",
            "kegg_string": "",
            "interpro_string": "",
            "retrieved_docs": [],
            "retrieve_context": "",
            "follow_up_questions": [],
            "final_response": {},
        }
        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )
        return final_state["final_response"]


async def brief_gene_function(
    user_query: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Run the LangGraph brief gene function workflow.

    Args:
        user_query: Gene identifier, symbol, or free-text query.
        **kwargs: Optional chat, retrieval, BI, retry, credential, and
            cache-fingerprint overrides.

    Returns:
        Chat-completions-style final response payload from BriefGeneAgent.
    """
    brief_config = copy_config_with_overrides(
        BRIEF_CONFIG,
        kwargs,
        BRIEF_GENE_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        get_sensitive_config(),
        kwargs,
        field_map=BRIEF_GENE_SENSITIVE_FIELD_MAP,
        secret_field_map=BRIEF_GENE_SECRET_FIELD_MAP,
    )
    agent = get_cached_agent(
        "BriefGeneAgent",
        lambda: BriefGeneAgent(
            brief_config=brief_config,
            sensitive_config=sensitive_config,
            knowledge_agent=KnowledgeAgent(
                knowledge_config=brief_config,
                sensitive_config=sensitive_config,
            ),
        ),
        agent_fingerprint_values(
            brief_config=brief_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(user_query=user_query)
