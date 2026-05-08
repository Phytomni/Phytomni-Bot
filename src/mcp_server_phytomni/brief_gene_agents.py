# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         maoyc_0316 (maoyc_0316@163.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Brief gene function summaries from BI annotations and literature RAG."""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict

from httpx import (
    AsyncClient,
    Timeout,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from .chat_agents import phyto_chat
from .config.defaults import BriefGeneConfig
from .config.overrides import (
    CHAT_COMPLETION_CONFIG_FIELD_MAP,
    RETRIEVAL_CONFIG_FIELD_MAP,
    RETRY_CONFIG_FIELD_MAP,
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .func_cache import func_cache
from .knowledge_agents import KnowledgeAgent
from .runtime.agent_registry import agent_fingerprint_values, get_cached_agent
from .runtime.langgraph_runner import ainvoke_graph, ensure_checkpointer
from .utils import (
    JsonPostRequest,
    JsonPostRetry,
    attach_message_payload,
    get_prompt,
    message_content,
    parse_follow_up_questions,
    post_json_with_retries,
)

BRIEF_CONFIG = BriefGeneConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
GENE_RETRIEVE_CACHE_TTL = 300

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
    """Cache-key-safe options for one gene literature retrieval request."""

    species: str
    symbols: tuple[str, ...]
    top_n: int
    agent_context: tuple[tuple[str, Any], ...]


def _sql_literal(value: str) -> str:
    """Return a single-quoted SQL literal with basic quote escaping."""
    return "'" + value.replace("'", "''") + "'"


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
    """Invoke the BI API to retrieve annotation information."""
    bi_url = kwargs.get("bi_url", BRIEF_CONFIG.BI_URL)
    bi_token = kwargs.get(
        "bi_token", SENSITIVE_CONFIG.BI_TOKEN.get_secret_value()
    )
    timeout = kwargs.get("timeout", BRIEF_CONFIG.TIMEOUT)
    retriable_codes = kwargs.get("retriable_codes")
    max_retries = kwargs.get("max_retries", BRIEF_CONFIG.MAX_RETRIES)
    if retriable_codes is None:
        retriable_codes = list(BRIEF_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
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
        if isinstance(data, dict):
            return data

    raise McpError(
        ErrorData(
            code=INTERNAL_ERROR,
            message="Failed to query BI API after all retries",
        )
    )


async def gene_retrieve(
    species: str,
    gene_symbol_list: List[str],
    knowledge_agent: KnowledgeAgent,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Retrieve literature for a gene through the LangGraph KnowledgeAgent."""
    top_n = kwargs.get("top_n", BRIEF_CONFIG.TOP_N)
    semaphore = kwargs.get("semaphore")
    symbols = tuple(_dedupe(gene_symbol_list))
    if not symbols:
        return {"doc_list": [], "total": 10000}

    agent_context = agent_fingerprint_values(
        knowledge_config=knowledge_agent.knowledge_config,
    )
    request = GeneRetrieveRequest(
        species=species,
        symbols=symbols,
        top_n=top_n,
        agent_context=tuple(sorted(agent_context.items())),
    )
    return await _gene_retrieve_cached(
        request=request,
        knowledge_agent=knowledge_agent,
        semaphore=semaphore,
    )


@func_cache(
    key_params=["request"],
    ttl=GENE_RETRIEVE_CACHE_TTL,
    exclude_params=["knowledge_agent", "semaphore"],
)
async def _gene_retrieve_cached(
    request: GeneRetrieveRequest,
    knowledge_agent: KnowledgeAgent,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Retrieve and cache gene literature for stable gene symbol queries."""
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
    """Clear cached gene literature retrieval results for tests/admin."""
    _gene_retrieve_cached.cache_clear()


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
    """State schema for the brief gene LangGraph workflow."""

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
    """LangGraph-based agent for brief gene function analysis."""

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        brief_config: BriefGeneConfig = BRIEF_CONFIG,
        sensitive_config: SensitiveConfig = SENSITIVE_CONFIG,
        knowledge_agent: Optional[KnowledgeAgent] = None,
    ):
        self.brief_config = brief_config
        self.sensitive_config = sensitive_config
        self.ka = knowledge_agent or KnowledgeAgent(
            knowledge_config=brief_config,
            sensitive_config=sensitive_config,
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
        """Route to annotation lookup only when BI found the gene."""
        if state["gene_found"]:
            return "fetch_annotation_node"
        return "retrieve_node"

    async def query_judge_node(self, state: BriefGeneAgentState):
        """Check whether the query is known to the BI gene ID table."""
        user_query = state["user_query"]
        query_response = await run_bi_api(
            "SELECT * FROM id2multispecies "
            f"WHERE query_id = {_sql_literal(user_query)}",
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
                f"WHERE query_id = {_sql_literal(gene_id)}",
                bi_url=self.brief_config.BI_URL,
                bi_token=self.sensitive_config.BI_TOKEN.get_secret_value(),
                timeout=self.brief_config.TIMEOUT,
                retriable_codes=self.brief_config.RETRIABLE_CODES,
                max_retries=self.brief_config.MAX_RETRIES,
            ),
            run_bi_api(
                "SELECT * FROM species "
                f"WHERE species_code = {_sql_literal(species_code)}",
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
        """Fetch gene annotation from BI database tables."""
        gene_id_literal = _sql_literal(state["gene_id"])
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
        """Retrieve gene literature through the LangGraph KnowledgeAgent."""
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
        """Generate the brief gene function report."""
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
        """Generate follow-up questions for the final report."""
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
        """Execute the BriefGeneAgent workflow."""
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
    """Compatibility wrapper around the LangGraph BriefGeneAgent."""
    brief_config = copy_config_with_overrides(
        BRIEF_CONFIG,
        kwargs,
        BRIEF_GENE_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        SENSITIVE_CONFIG,
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
