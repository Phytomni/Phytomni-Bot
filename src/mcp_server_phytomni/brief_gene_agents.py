# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: lihu (lihu0628@qq.com)
#         xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""Brief gene function summaries from BI annotations and literature RAG."""

import asyncio
from json import loads
from random import uniform
from typing import Any, Dict, List, Optional, TypedDict, Union

from httpx import AsyncClient, ConnectError, HTTPStatusError
from httpx import Timeout, TimeoutException
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INTERNAL_ERROR

from .agent_registry import agent_fingerprint_values, get_cached_agent
from .chat_agents import phyto_chat
from .config.defaults import BriefGeneConfig
from .config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .func_cache import func_cache
from .knowledge_agents import KnowledgeAgent
from .langgraph_runner import ainvoke_graph, ensure_checkpointer
from .utils import get_prompt

bgc = BriefGeneConfig()
sc = SensitiveConfig.load()
GENE_RETRIEVE_CACHE_TTL = 300

BRIEF_GENE_CONFIG_FIELD_MAP = {
    "prompt_file": "PROMPT_FILE",
    "prompt_path": "PROMPT_PATH",
    "frequency_penalty": "FREQUENCY_PENALTY",
    "n": "N",
    "presence_penalty": "PRESENCE_PENALTY",
    "reasoning_effort": "REASONING_EFFORT",
    "response_format": "RESPONSE_FORMAT",
    "stream": "STREAM",
    "temperature": "TEMPERATURE",
    "top_p": "TOP_P",
    "user": "USER",
    "retrieve_url": "RETRIEVE_URL",
    "repo_id_dict": "REPO_ID_DICT",
    "page_num": "PAGE_NUM",
    "filter_string": "FILTER_STRING",
    "scope": "SCOPE",
    "extra_repo_ids": "EXTRA_REPO_IDS",
    "rerank_url": "RERANK_URL",
    "rerank_batch_size": "RERANK_BATCH_SIZE",
    "score_threshold": "SCORE_THRESHOLD",
    "top_n": "TOP_N",
    "bi_url": "BI_URL",
    "max_concurrency": "MAX_CONCURRENCY",
    "timeout": "TIMEOUT",
    "retriable_codes": "RETRIABLE_CODES",
    "max_retries": "MAX_RETRIES",
    "max_tokens": "MAX_TOKENS",
}
BRIEF_GENE_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
BRIEF_GENE_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
    "bi_token": "BI_TOKEN",
}


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


def _parse_follow_up_questions(text: str) -> List[str]:
    """Parse follow-up questions from a JSON list embedded in model output."""
    if not text:
        return []
    start_index = text.find("[")
    end_index = text.rfind("]") + 1
    if start_index == -1 or end_index <= start_index:
        return []
    try:
        parsed = loads(text[start_index:end_index])
    except (ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _message_content(response: Any) -> str:
    """Return the first assistant message content from an OpenAI-style dict."""
    if (
        isinstance(response, dict)
        and response.get("choices")
        and isinstance(response["choices"], list)
        and response["choices"][0]
        and isinstance(response["choices"][0], dict)
        and isinstance(response["choices"][0].get("message"), dict)
    ):
        return str(response["choices"][0]["message"].get("content", ""))
    return ""


def _attach_metadata(
    phyto_response: Dict[str, Any],
    doc_list: List[Dict[str, Any]],
    follow_up_questions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Attach references and follow-up questions to a model response."""
    if not isinstance(phyto_response, dict) or "choices" not in phyto_response:
        phyto_response = {"choices": [{"message": {}}]}
    if not phyto_response["choices"]:
        phyto_response["choices"].append({"message": {}})
    if "message" not in phyto_response["choices"][0]:
        phyto_response["choices"][0]["message"] = {}

    payload: Dict[str, Any] = {"doc_list": doc_list, "total": 10000}
    if follow_up_questions is not None:
        payload["follow_up_questions"] = follow_up_questions
    phyto_response["choices"][0]["message"].update(payload)
    return phyto_response


async def run_bi_api(
    query_sql: str,
    bi_url: str = bgc.BI_URL,
    bi_token: str = sc.BI_TOKEN.get_secret_value(),
    timeout: float = bgc.TIMEOUT,
    retriable_codes: List[int] = bgc.RETRIABLE_CODES,
    max_retries: int = bgc.MAX_RETRIES,
) -> Dict[str, Any]:
    """Invoke the BI API to retrieve annotation information."""
    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    bi_url,
                    headers={
                        "Content-Type": "application/json",
                        "token": bi_token,
                    },
                    json={"sql": query_sql, "returnType": "json"},
                    timeout=timeout,
                )
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, dict) else {}
            except HTTPStatusError as exc:
                if (
                    exc.response is not None
                    and exc.response.status_code in retriable_codes
                    and attempt < max_retries
                ):
                    await asyncio.sleep((2**attempt) + uniform(0, 1))
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Failed to query BI API: {str(exc)}",
                    )
                ) from exc
            except (ConnectError, TimeoutException) as exc:
                if attempt < max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"BI API network error: {str(exc)}",
                    )
                ) from exc

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
    top_n: int = bgc.TOP_N,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Retrieve literature for a gene through the LangGraph KnowledgeAgent."""
    symbols = tuple(_dedupe(gene_symbol_list))
    if not symbols:
        return {"doc_list": [], "total": 10000}

    agent_context = agent_fingerprint_values(
        knowledge_config=knowledge_agent.kc,
    )
    return await _gene_retrieve_cached(
        species=species,
        symbols=symbols,
        top_n=top_n,
        agent_context=agent_context,
        knowledge_agent=knowledge_agent,
        semaphore=semaphore,
    )


@func_cache(
    key_params=["species", "symbols", "top_n", "agent_context"],
    ttl=GENE_RETRIEVE_CACHE_TTL,
    exclude_params=["knowledge_agent", "semaphore"],
)
async def _gene_retrieve_cached(
    species: str,
    symbols: tuple[str, ...],
    top_n: int,
    agent_context: Dict[str, Any],
    knowledge_agent: KnowledgeAgent,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Retrieve and cache gene literature for stable gene symbol queries."""
    del agent_context
    combined_symbols = "\n".join(symbols)
    query_terms = _dedupe([*symbols, combined_symbols])

    async def make_gene_retrieve() -> Dict[str, Any]:
        tasks = [
            knowledge_agent.arun(
                user_query=f"{species}\n{symbol}",
                is_generate=False,
                is_follow_up=False,
            )
            for symbol in query_terms
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged_docs = []
        seen = set()
        for result in results:
            if not isinstance(result, dict):
                continue
            for doc in result.get("doc_list", []):
                doc_key = doc.get("chunk_id") or (
                    doc.get("title"),
                    doc.get("content"),
                )
                if doc_key in seen:
                    continue
                seen.add(doc_key)
                merged_docs.append(doc)
        sorted_docs = sorted(
            merged_docs, key=lambda item: item.get("score", 0), reverse=True
        )
        if top_n is not None and top_n > 0:
            sorted_docs = sorted_docs[:top_n]
        return {"doc_list": sorted_docs, "total": 10000}

    if semaphore is not None:
        async with semaphore:
            return await make_gene_retrieve()
    return await make_gene_retrieve()


async def _generate_follow_up(
    user_query: str,
    phyto_response: Dict[str, Any],
    prompt_file: str,
    prompt_path: str,
    api_key: str,
    base_url: str,
    model: str,
    frequency_penalty: float,
    n: int,
    presence_penalty: float,
    reasoning_effort: Optional[str],
    response_format: Dict[str, Union[str, Dict]],
    stream: bool,
    temperature: float,
    top_p: float,
    user: str,
    timeout: float,
    retriable_codes: List[int],
    max_retries: int,
) -> List[str]:
    """Generate follow-up questions for a brief gene response."""
    follow_up_response = await phyto_chat(
        user_query=get_prompt(
            prompt_file,
            "system/follow_up_questions",
            {
                "user_query": f"What is gene function of {user_query}",
                "system_response": _message_content(phyto_response),
            },
        ),
        prompt_file=prompt_file,
        prompt_path=prompt_path,
        api_key=api_key,
        base_url=base_url,
        model=model,
        frequency_penalty=frequency_penalty,
        n=n,
        presence_penalty=presence_penalty,
        reasoning_effort=reasoning_effort,
        response_format=response_format,
        stream=stream,
        temperature=temperature,
        top_p=top_p,
        user=user,
        timeout=timeout,
        retriable_codes=retriable_codes,
        max_retries=max_retries,
    )
    return _parse_follow_up_questions(_message_content(follow_up_response))


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
        brief_config: BriefGeneConfig = bgc,
        sensitive_config: SensitiveConfig = sc,
        knowledge_agent: Optional[KnowledgeAgent] = None,
    ):
        self.bgc = brief_config
        self.sc = sensitive_config
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
            bi_url=self.bgc.BI_URL,
            bi_token=self.sc.BI_TOKEN.get_secret_value(),
            timeout=self.bgc.TIMEOUT,
            retriable_codes=self.bgc.RETRIABLE_CODES,
            max_retries=self.bgc.MAX_RETRIES,
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
                bi_url=self.bgc.BI_URL,
                bi_token=self.sc.BI_TOKEN.get_secret_value(),
                timeout=self.bgc.TIMEOUT,
                retriable_codes=self.bgc.RETRIABLE_CODES,
                max_retries=self.bgc.MAX_RETRIES,
            ),
            run_bi_api(
                "SELECT * FROM species "
                f"WHERE species_code = {_sql_literal(species_code)}",
                bi_url=self.bgc.BI_URL,
                bi_token=self.sc.BI_TOKEN.get_secret_value(),
                timeout=self.bgc.TIMEOUT,
                retriable_codes=self.bgc.RETRIABLE_CODES,
                max_retries=self.bgc.MAX_RETRIES,
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
                    bi_url=self.bgc.BI_URL,
                    bi_token=self.sc.BI_TOKEN.get_secret_value(),
                    timeout=self.bgc.TIMEOUT,
                    retriable_codes=self.bgc.RETRIABLE_CODES,
                    max_retries=self.bgc.MAX_RETRIES,
                )
                for sql in annotation_sqls
            ],
            return_exceptions=True,
        )

        id_rows = (
            _response_data(annotation_responses[0])
            if not isinstance(annotation_responses[0], Exception)
            else []
        )
        gene_symbols = _split_symbols(
            str(id_rows[0].get("symbol", "")) if id_rows else ""
        )
        gene_id_list = _dedupe(
            [state["user_query"], state["gene_id"], *gene_symbols]
        )

        structure_rows = (
            _response_data(annotation_responses[1])
            if not isinstance(annotation_responses[1], Exception)
            else []
        )
        structure_row = structure_rows[0] if structure_rows else {}

        go_rows = (
            _response_data(annotation_responses[2])
            if not isinstance(annotation_responses[2], Exception)
            else []
        )
        core_go_rows = [
            row
            for row in go_rows
            if str(row.get("is_propagated_from_child_term")) == "0"
        ] or [
            row
            for row in go_rows
            if str(row.get("is_propagated_from_child_term")) == "1"
        ]
        go_string = (
            " ; ".join(
                _dedupe(
                    [
                        f"{row.get('go_id')} ({row.get('go_name')})"
                        for row in core_go_rows
                        if row.get("go_id") or row.get("go_name")
                    ]
                )
            )
            or "No annotation available."
        )

        mapman_rows = (
            _response_data(annotation_responses[3])
            if not isinstance(annotation_responses[3], Exception)
            else []
        )
        invalid_keywords = ["not assigned", "unknown", "not annotate"]
        kegg_string = (
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

        interpro_rows = (
            _response_data(annotation_responses[4])
            if not isinstance(annotation_responses[4], Exception)
            else []
        )
        interpro_string = (
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
                top_n=self.bgc.TOP_N,
                semaphore=asyncio.Semaphore(self.bgc.MAX_CONCURRENCY),
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
            "retrieve_context": _format_docs(doc_list, self.bgc.MAX_TOKENS),
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
                self.bgc.PROMPT_FILE, "user/brief_gene_function", prompt_vars
            )
        else:
            chat_query = get_prompt(
                self.bgc.PROMPT_FILE,
                "user/brief_gene_function_nogeneid",
                {
                    "user_query": state["user_query"],
                    "retrieve_results": state["retrieve_context"],
                },
            )

        phyto_response = await phyto_chat(
            user_query=chat_query,
            prompt_file=self.bgc.PROMPT_FILE,
            prompt_path=self.bgc.PROMPT_PATH,
            api_key=self.sc.API_KEY.get_secret_value(),
            base_url=self.sc.BASE_URL,
            model=self.sc.MODEL_ID,
            frequency_penalty=self.bgc.FREQUENCY_PENALTY,
            n=self.bgc.N,
            presence_penalty=self.bgc.PRESENCE_PENALTY,
            reasoning_effort=self.bgc.REASONING_EFFORT,
            response_format=self.bgc.RESPONSE_FORMAT,
            stream=self.bgc.STREAM,
            temperature=self.bgc.TEMPERATURE,
            top_p=self.bgc.TOP_P,
            user=self.bgc.USER,
            timeout=self.bgc.TIMEOUT,
            retriable_codes=self.bgc.RETRIABLE_CODES,
            max_retries=self.bgc.MAX_RETRIES,
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
            prompt_file=self.bgc.PROMPT_FILE,
            prompt_path=self.bgc.PROMPT_PATH,
            api_key=self.sc.API_KEY.get_secret_value(),
            base_url=self.sc.BASE_URL,
            model=self.sc.MODEL_ID,
            frequency_penalty=self.bgc.FREQUENCY_PENALTY,
            n=self.bgc.N,
            presence_penalty=self.bgc.PRESENCE_PENALTY,
            reasoning_effort=self.bgc.REASONING_EFFORT,
            response_format=self.bgc.RESPONSE_FORMAT,
            stream=self.bgc.STREAM,
            temperature=self.bgc.TEMPERATURE,
            top_p=self.bgc.TOP_P,
            user=self.bgc.USER,
            timeout=self.bgc.TIMEOUT,
            retriable_codes=self.bgc.RETRIABLE_CODES,
            max_retries=self.bgc.MAX_RETRIES,
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
    prompt_file: str = bgc.PROMPT_FILE,
    prompt_path: str = bgc.PROMPT_PATH,
    api_key: str = sc.API_KEY.get_secret_value(),
    base_url: str = sc.BASE_URL,
    model: str = sc.MODEL_ID,
    frequency_penalty: float = bgc.FREQUENCY_PENALTY,
    n: int = bgc.N,
    presence_penalty: float = bgc.PRESENCE_PENALTY,
    reasoning_effort: Optional[str] = bgc.REASONING_EFFORT,
    response_format: Dict[str, Union[str, Dict]] = bgc.RESPONSE_FORMAT,
    stream: bool = bgc.STREAM,
    temperature: float = bgc.TEMPERATURE,
    top_p: float = bgc.TOP_P,
    user: str = bgc.USER,
    retrieve_url: str = bgc.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = bgc.REPO_ID_DICT,
    page_num: int = bgc.PAGE_NUM,
    filter_string: Optional[str] = bgc.FILTER_STRING,
    scope: str = bgc.SCOPE,
    extra_repo_ids: Optional[List[str]] = bgc.EXTRA_REPO_IDS,
    rerank_url: str = bgc.RERANK_URL,
    rerank_batch_size: int = bgc.RERANK_BATCH_SIZE,
    score_threshold: float = bgc.SCORE_THRESHOLD,
    top_n: int = bgc.TOP_N,
    bi_url: str = bgc.BI_URL,
    bi_token: str = sc.BI_TOKEN.get_secret_value(),
    max_concurrency: int = bgc.MAX_CONCURRENCY,
    timeout: float = bgc.TIMEOUT,
    retriable_codes: List[int] = bgc.RETRIABLE_CODES,
    max_retries: int = bgc.MAX_RETRIES,
    max_tokens: int = bgc.MAX_TOKENS,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph BriefGeneAgent."""
    arguments = locals().copy()
    brief_config = copy_config_with_overrides(
        bgc,
        arguments,
        BRIEF_GENE_CONFIG_FIELD_MAP,
    )
    sensitive_config = copy_sensitive_config_with_overrides(
        sc,
        arguments,
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
