# Copyright (c) Biotechnology Research Institute,
# Chinese Academy of Agricultural Sciences. 2024-2026. All rights reserved.
# Author: xieshang (xieshang0608@gmail.com)
#         guxiaofeng (guxiaofeng@caas.cn)
"""This module provides functions for interacting with a knowledge base.

It includes functions for retrieving, reranking, and generating text based on
the retrieved knowledge.
"""

import asyncio
from random import uniform
from typing import Any, Dict, List, Literal, Optional, TypedDict

from httpx import (
    AsyncClient,
    ConnectError,
    HTTPStatusError,
    Timeout,
    TimeoutException,
)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from mcp.shared.exceptions import McpError
from mcp.types import INTERNAL_ERROR, ErrorData

from .agent_registry import agent_fingerprint_values, get_cached_agent
from .chat_agents import phyto_chat
from .config.defaults import KnowledgeConfig
from .config.overrides import (
    copy_config_with_overrides,
    copy_sensitive_config_with_overrides,
)
from .config.settings import SensitiveConfig
from .func_cache import func_cache
from .langgraph_runner import ainvoke_graph, ensure_checkpointer
from .utils import (
    download_list_convert,
    get_prompt,
    message_content,
    parse_follow_up_questions,
    split_list,
)

KNOWLEDGE_CONFIG = KnowledgeConfig()
SENSITIVE_CONFIG = SensitiveConfig.load()
RETRIEVE_CACHE_TTL = 300

KNOWLEDGE_CONFIG_FIELD_MAP = {
    "retrieve_url": "RETRIEVE_URL",
    "repo_id": "REPO_ID",
    "repo_id_dict": "REPO_ID_DICT",
    "page_num": "PAGE_NUM",
    "page_size": "PAGE_SIZE",
    "filter_string": "FILTER_STRING",
    "scope": "SCOPE",
    "extra_repo_ids": "EXTRA_REPO_IDS",
    "rerank_url": "RERANK_URL",
    "rerank_batch_size": "RERANK_BATCH_SIZE",
    "score_threshold": "SCORE_THRESHOLD",
    "top_n": "TOP_N",
    "prompt_file": "PROMPT_FILE",
    "prompt_path": "PROMPT_PATH",
    "frequency_penalty": "FREQUENCY_PENALTY",
    "max_tokens": "MAX_TOKENS",
    "n": "N",
    "presence_penalty": "PRESENCE_PENALTY",
    "reasoning_effort": "REASONING_EFFORT",
    "response_format": "RESPONSE_FORMAT",
    "stream": "STREAM",
    "temperature": "TEMPERATURE",
    "top_p": "TOP_P",
    "user": "USER",
    "server_dir": "TEMP_DIR",
    "obs_server": "OBS_SERVER",
    "bucket_name": "BUCKET_NAME",
    "part_size": "PART_SIZE",
    "task_num": "TASK_NUM",
    "max_concurrency": "MAX_CONCURRENCY",
    "max_workers": "MAX_WORKERS",
    "timeout": "TIMEOUT",
    "retriable_codes": "RETRIABLE_CODES",
    "max_retries": "MAX_RETRIES",
}
KNOWLEDGE_SENSITIVE_FIELD_MAP = {
    "base_url": "BASE_URL",
    "model": "MODEL_ID",
}
KNOWLEDGE_SECRET_FIELD_MAP = {
    "api_key": "API_KEY",
    "access_key_id": "ACCESS_KEY_ID",
    "secret_access_key": "SECRET_ACCESS_KEY",
}


class KnowledgeAgentState(TypedDict):
    """State schema for the KnowledgeAgent LangGraph workflow.

    This TypedDict defines the shared state that flows through each node
    in the KnowledgeAgent graph. Each node reads from and writes to this
    state as the graph processes a user's query.

    Attributes:
        user_query: The user's natural language query.
        obs_file_list: A list of OBS file paths uploaded by the user.
        repo_id_dict: A dictionary mapping repository names to their IDs.
        upload_context: The parsed content from user-uploaded files.
        retrieved_docs: Documents retrieved from the knowledge base.
        retrieve_context: The formatted retrieval context for the LLM.
        main_response: The initial response from the LLM (contains choices).
        is_generate: Whether to generate a response after retrieval.
        is_follow_up: Whether to generate follow-up questions.
        follow_up_questions: A list of suggested follow-up questions.
        final_response: The final merged response returned to the user.
    """

    user_query: str
    obs_file_list: Optional[List[str]]
    repo_id_dict: Optional[Dict[str, int]]
    upload_context: str
    retrieved_docs: List[Dict[str, Any]]
    retrieve_context: str
    main_response: Dict[str, Any]
    is_generate: bool
    is_follow_up: bool
    follow_up_questions: List[dict]
    final_response: Dict[str, Any]


class KnowledgeAgent:
    """A LangGraph-based agent for knowledge base retrieval and generation.

    This agent orchestrates a workflow that processes user queries,
    retrieves relevant documents from a knowledge base, and generates
    responses using an LLM. It supports optional file uploads via OBS
    and can generate follow-up questions based on the initial response.

    The workflow graph consists of four main nodes:
        1. process_files_node: Downloads and parses user-uploaded OBS files.
        2. retrieve_node: Retrieves and reranks documents from knowledge bases.
        3. generate_node: Generates a response using the retrieved context.
        4. follow_up_node: Generates suggested follow-up questions.

    Args:
        checkpointer: A LangGraph checkpointer for state persistence.
                      Defaults to a fresh MemorySaver instance.
        knowledge_config: Configuration for knowledge base retrieval.
                          Defaults to the global KNOWLEDGE_CONFIG instance.
        sensitive_config: Configuration for sensitive data (e.g., credentials).
                          Defaults to the global SENSITIVE_CONFIG instance.

    Attributes:
        KNOWLEDGE_CONFIG: The knowledge configuration instance.
        SENSITIVE_CONFIG: The sensitive configuration instance.
        checkpointer: The checkpointer for state persistence.
        app: The compiled LangGraph application.
    """

    def __init__(
        self,
        checkpointer: Optional[MemorySaver] = None,
        knowledge_config=KNOWLEDGE_CONFIG,
        sensitive_config=SENSITIVE_CONFIG,
    ):
        """Initialize the KnowledgeAgent and build the graph."""
        self.knowledge_config = knowledge_config
        self.sensitive_config = sensitive_config
        self.checkpointer = ensure_checkpointer(checkpointer)
        self.app = self._build_graph()

    def _build_graph(self):
        """Build and compile the LangGraph StateGraph workflow.

        This method constructs the workflow graph by adding nodes,
        defining edges, and setting up conditional routing. The resulting
        graph orchestrates the retrieval and generation pipeline.

        Returns:
            A compiled StateGraph with checkpointer support.
        """
        workflow = StateGraph(KnowledgeAgentState)
        workflow.add_node("process_files_node", self.process_files_node)
        workflow.add_node("retrieve_node", self.retrieve_node)
        workflow.add_node("generate_node", self.generate_node)
        workflow.add_node("follow_up_node", self.follow_up_node)

        workflow.add_conditional_edges(START, self.route_start)
        workflow.add_edge("process_files_node", "retrieve_node")
        workflow.add_conditional_edges(
            "retrieve_node", self.route_after_retrieve
        )
        workflow.add_conditional_edges(
            "generate_node", self.route_after_generate
        )
        workflow.add_edge("follow_up_node", END)

        return workflow.compile(checkpointer=self.checkpointer)

    async def process_files_node(self, state: KnowledgeAgentState):
        """Process OBS files and convert them to Markdown context.

        This node downloads files from OBS storage, converts them to text,
        and adds them to the state as upload_context. The content is
        truncated if it exceeds the maximum token limit defined in the
        knowledge configuration.

        Args:
            state: The current workflow state containing obs_file_list.

        Returns:
            A dictionary containing the upload_context key with the
            parsed file contents.
        """
        obs_file_list = state.get("obs_file_list", [])
        upload_context = ""
        total_length = 0

        if obs_file_list:
            access_key_id, secret_access_key = (
                self.sensitive_config.obs_credentials()
            )
            upload_str_list = await download_list_convert(
                obs_file_list=obs_file_list,
                server_dir=self.knowledge_config.TEMP_DIR,
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
                obs_server=self.knowledge_config.OBS_SERVER,
                bucket_name=self.knowledge_config.BUCKET_NAME,
                part_size=self.knowledge_config.PART_SIZE,
                task_num=self.knowledge_config.TASK_NUM,
                max_retries=self.knowledge_config.MAX_RETRIES,
                max_concurrency=self.knowledge_config.MAX_CONCURRENCY,
                max_workers=self.knowledge_config.MAX_WORKERS,
            )
            upload_results = []
            for i, doc in enumerate(upload_str_list):
                fragment = (
                    f"[user upload file {i+1} begin]\n"
                    f"{doc}\n[user upload file {i+1} end]"
                )
                if (
                    total_length + len(fragment)
                    <= self.knowledge_config.MAX_TOKENS
                ):
                    upload_results.append(fragment)
                    total_length += len(fragment)
                else:
                    break
            upload_context = "\n\n".join(upload_results)

        return {"upload_context": upload_context}

    async def retrieve_node(self, state: KnowledgeAgentState):
        """Retrieve and rerank documents from the knowledge base.

        This node queries multiple knowledge repositories, merges the results,
        and formats them into a context string suitable for the LLM. It uses
        the multi_retrieve function to perform parallel retrieval across
        multiple repositories with reranking based on relevance scores.

        Args:
            state: The current workflow state containing user_query,
                   repo_id_dict, and upload_context.

        Returns:
            A dictionary containing:
                - retrieved_docs: The raw list of retrieved documents.
                - retrieve_context: The formatted context string for the LLM.
        """
        user_query = state["user_query"]
        repo_id_dict = (
            state.get("repo_id_dict") or self.knowledge_config.REPO_ID_DICT
        )
        upload_context = state.get("upload_context", "")

        retrieve_response = await multi_retrieve(
            user_query=user_query,
            retrieve_url=self.knowledge_config.RETRIEVE_URL,
            repo_id_dict=repo_id_dict,
            page_num=self.knowledge_config.PAGE_NUM,
            filter_string=self.knowledge_config.FILTER_STRING,
            scope=self.knowledge_config.SCOPE,
            extra_repo_ids=self.knowledge_config.EXTRA_REPO_IDS,
            rerank_url=self.knowledge_config.RERANK_URL,
            rerank_batch_size=self.knowledge_config.RERANK_BATCH_SIZE,
            score_threshold=self.knowledge_config.SCORE_THRESHOLD,
            top_n=self.knowledge_config.TOP_N,
            timeout=self.knowledge_config.TIMEOUT,
            retriable_codes=self.knowledge_config.RETRIABLE_CODES,
            max_retries=self.knowledge_config.MAX_RETRIES,
        )

        retrieve_results = []
        total_length = len(upload_context)
        for i, doc in enumerate(retrieve_response.get("doc_list", [])):
            header = f"[document {i+1} begin] {doc['title']}"
            content_field = (
                doc.get("big_content")
                if "big_content" in doc
                else doc.get("content", "")
            )
            body = (
                f"{doc['subtitle']}\n{content_field}"
                if doc.get("subtitle")
                else doc.get("content", "")
            )
            fragment = f"{header}\n{body} [document {i+1} end]"
            if (
                total_length + len(fragment)
                <= self.knowledge_config.MAX_TOKENS
            ):
                retrieve_results.append(fragment)
                total_length += len(fragment)
            else:
                break

        retrieve_context = "\n\n".join(retrieve_results)

        return {
            "retrieved_docs": retrieve_response.get("doc_list", []),
            "retrieve_context": retrieve_context,
        }

    async def generate_node(self, state: KnowledgeAgentState):
        """Generate a response based on retrieved documents and user files.

        This node constructs a prompt using the retrieved context and any
        uploaded file content, then sends it to the LLM for response
        generation.
        The retrieved documents are attached to the response for reference.

        Args:
            state: The current workflow state containing user_query,
                   retrieve_context, upload_context, and retrieved_docs.

        Returns:
            A dictionary containing:
                - main_response: The LLM response with document references.
                - final_response: The same response (may be updated later).
        """
        user_query = state["user_query"]
        retrieve_context = state["retrieve_context"]
        upload_context = state.get("upload_context", "")

        if upload_context:
            chat_query = get_prompt(
                self.knowledge_config.PROMPT_FILE,
                "user/retrieval_file",
                {
                    "retrieve_results": retrieve_context,
                    "upload_context": upload_context,
                    "user_query": user_query,
                },
            )
        else:
            chat_query = get_prompt(
                self.knowledge_config.PROMPT_FILE,
                "user/retrieval",
                {
                    "retrieve_results": retrieve_context,
                    "user_query": user_query,
                },
            )

        phyto_response = await phyto_chat(
            user_query=chat_query,
            prompt_file=self.knowledge_config.PROMPT_FILE,
            prompt_path=self.knowledge_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.knowledge_config.FREQUENCY_PENALTY,
            n=self.knowledge_config.N,
            presence_penalty=self.knowledge_config.PRESENCE_PENALTY,
            reasoning_effort=self.knowledge_config.REASONING_EFFORT,
            response_format=self.knowledge_config.RESPONSE_FORMAT,
            stream=self.knowledge_config.STREAM,
            temperature=self.knowledge_config.TEMPERATURE,
            top_p=self.knowledge_config.TOP_P,
            user=self.knowledge_config.USER,
            timeout=self.knowledge_config.TIMEOUT,
            retriable_codes=self.knowledge_config.RETRIABLE_CODES,
            max_retries=self.knowledge_config.MAX_RETRIES,
        )

        # 将 doc_list 挂载到大模型返回的 message 中
        doc_list_payload = {
            "doc_list": state["retrieved_docs"],
            "total": 10000,
        }

        if (
            phyto_response
            and "choices" in phyto_response
            and len(phyto_response["choices"]) > 0
        ):
            if (
                "message" in phyto_response["choices"][0]
                and phyto_response["choices"][0]["message"] is not None
            ):
                phyto_response["choices"][0]["message"].update(
                    doc_list_payload
                )
            else:
                phyto_response["choices"][0]["message"] = doc_list_payload
        else:
            if phyto_response is None:
                phyto_response = {"choices": [{"message": doc_list_payload}]}
            elif "choices" not in phyto_response:
                phyto_response["choices"] = [{"message": doc_list_payload}]
            elif len(phyto_response["choices"]) == 0:
                phyto_response["choices"].append({"message": doc_list_payload})

        return {
            "main_response": phyto_response,
            "final_response": phyto_response,
        }

    async def follow_up_node(self, state: KnowledgeAgentState):
        """Generate suggested follow-up questions.

        This node analyzes the initial LLM response and generates relevant
        follow-up questions that the user might want to ask. Questions are
        parsed from the LLM output and attached to the final response.

        Args:
            state: The current workflow state containing user_query
                   and main_response.

        Returns:
            A dictionary containing:
                - follow_up_questions: A list of suggested questions.
                - final_response: Response with follow-up questions.
        """
        user_query = state["user_query"]
        phyto_response = state["main_response"]
        system_response_text = message_content(phyto_response)

        follow_up_response = await phyto_chat(
            user_query=get_prompt(
                self.knowledge_config.PROMPT_FILE,
                "system/follow_up_questions",
                {
                    "user_query": user_query,
                    "system_response": system_response_text,
                },
            ),
            prompt_file=self.knowledge_config.PROMPT_FILE,
            prompt_path=self.knowledge_config.PROMPT_PATH,
            api_key=self.sensitive_config.API_KEY.get_secret_value(),
            base_url=self.sensitive_config.BASE_URL,
            model=self.sensitive_config.MODEL_ID,
            frequency_penalty=self.knowledge_config.FREQUENCY_PENALTY,
            n=self.knowledge_config.N,
            presence_penalty=self.knowledge_config.PRESENCE_PENALTY,
            reasoning_effort=self.knowledge_config.REASONING_EFFORT,
            response_format=self.knowledge_config.RESPONSE_FORMAT,
            stream=self.knowledge_config.STREAM,
            temperature=self.knowledge_config.TEMPERATURE,
            top_p=self.knowledge_config.TOP_P,
            user=self.knowledge_config.USER,
            timeout=self.knowledge_config.TIMEOUT,
            retriable_codes=self.knowledge_config.RETRIABLE_CODES,
            max_retries=self.knowledge_config.MAX_RETRIES,
        )

        follow_up_list = parse_follow_up_questions(
            message_content(follow_up_response)
        )

        # 更新最终返回值
        phyto_response["choices"][0]["message"].update(
            {"follow_up_questions": follow_up_list}
        )

        return {
            "follow_up_questions": follow_up_list,
            "final_response": phyto_response,
        }

    def route_start(
        self, state: KnowledgeAgentState
    ) -> Literal["process_files_node", "retrieve_node"]:
        """Route from the START node based on whether files are uploaded.

        This method determines the first node to execute based on the
        presence of user-uploaded OBS files.

        Args:
            state: The current workflow state.

        Returns:
            "process_files_node" if files are uploaded, otherwise
            "retrieve_node".
        """
        obs_file_list = state.get("obs_file_list")
        if obs_file_list and len(obs_file_list) > 0:
            return "process_files_node"
        return "retrieve_node"

    def route_after_retrieve(
        self, state: KnowledgeAgentState
    ) -> Literal["generate_node", "__end__"]:
        """Route after the retrieve node based on generation flag.

        This method determines whether to proceed to the generate node
        or end the workflow based on the is_generate flag.

        Args:
            state: The current workflow state.

        Returns:
            "generate_node" if is_generate is True, otherwise "__end__".
        """
        if state["is_generate"]:
            return "generate_node"
        return "__end__"

    def route_after_generate(
        self, state: KnowledgeAgentState
    ) -> Literal["follow_up_node", "__end__"]:
        """Route after the generate node based on follow-up flag.

        This method determines whether to proceed to the follow-up node
        or end the workflow based on the is_follow_up flag.

        Args:
            state: The current workflow state.

        Returns:
            "follow_up_node" if is_follow_up is True, otherwise "__end__".
        """
        if state["is_follow_up"]:
            return "follow_up_node"
        return "__end__"

    async def arun(
        self,
        user_query: str,
        obs_file_list: Optional[List[str]] = None,
        repo_id_dict: Optional[Dict[str, int]] = None,
        is_generate: bool = True,
        is_follow_up: bool = True,
        thread_id: Optional[str] = None,
    ):
        """Execute the KnowledgeAgent workflow.

        This is the main entry point for invoking the agent. It initializes
        the state with the user's query and optional parameters, then runs
        the LangGraph workflow.

        Args:
            user_query: The user's natural language query.
            obs_file_list: Optional list of OBS file paths to upload.
            repo_id_dict: Optional dictionary mapping repo names to IDs.
            is_generate: Whether to generate a response. Defaults to True.
            is_follow_up: Whether to generate follow-up questions. Defaults
                to True.
            thread_id: Optional thread ID for state persistence. If not
                provided, a new UUID will be generated.

        Returns:
            The final response dictionary containing the LLM response
            and optionally the doc_list and follow_up_questions.
        """
        initial_state = {
            "user_query": user_query,
            "obs_file_list": obs_file_list or [],
            "repo_id_dict": repo_id_dict,
            "upload_context": "",
            "retrieved_docs": [],
            "retrieve_context": "",
            "main_response": {},
            "is_generate": is_generate,
            "is_follow_up": is_follow_up,
            "follow_up_questions": [],
            "final_response": {},
        }

        final_state = await ainvoke_graph(
            self.app, initial_state, thread_id=thread_id
        )

        if not is_generate:
            return final_state["retrieved_docs"]
        return final_state["final_response"]


@func_cache(
    key_params=[
        "user_query",
        "retrieve_url",
        "repo_id",
        "page_num",
        "page_size",
        "filter_string",
        "scope",
        "extra_repo_ids",
        "rerank_url",
        "rerank_batch_size",
        "score_threshold",
    ],
    ttl=RETRIEVE_CACHE_TTL,
)
async def retrieve(
    user_query: str,
    retrieve_url: str = KNOWLEDGE_CONFIG.RETRIEVE_URL,
    repo_id: str = KNOWLEDGE_CONFIG.REPO_ID,
    page_num: int = KNOWLEDGE_CONFIG.PAGE_NUM,
    page_size: int = KNOWLEDGE_CONFIG.PAGE_SIZE,
    filter_string: Optional[str] = KNOWLEDGE_CONFIG.FILTER_STRING,
    scope: str = KNOWLEDGE_CONFIG.SCOPE,
    extra_repo_ids: Optional[List[str]] = KNOWLEDGE_CONFIG.EXTRA_REPO_IDS,
    rerank_url: str = KNOWLEDGE_CONFIG.RERANK_URL,
    rerank_batch_size: int = KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE,
    score_threshold: float = KNOWLEDGE_CONFIG.SCORE_THRESHOLD,
    timeout: float = KNOWLEDGE_CONFIG.TIMEOUT,
    retriable_codes: Optional[List[int]] = None,
    max_retries: int = KNOWLEDGE_CONFIG.MAX_RETRIES,
) -> Dict[str, Any]:
    """Retrieve and rerank documents from a knowledge base.

    This function queries a knowledge base service, retrieves documents based
    on the user query, and then reranks them to improve relevance. It supports
    searching within document content, keywords, or both. The function also
    includes a retry mechanism for transient network or server errors.

    Args:
        user_query: The user's natural language query.
        retrieve_url: The URL of the retrieval service.
        repo_id: The ID of the primary knowledge repository to search.
        page_num: The page number for pagination of retrieval results.
        page_size: The number of documents to retrieve per page. This also
                   serves as the `top_n` parameter for the reranking process.
        filter_string: An optional string for metadata filtering.
        scope: The search scope, which can be 'doc', 'keyword', or 'both'.
        extra_repo_ids: An optional list of additional repository IDs to
                        include in the search.
        rerank_url: The URL of the reranking service.
        rerank_batch_size: The batch size for reranking documents.
        score_threshold: The minimum relevance score to include documents in
                         the final result.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.

    Returns:
        A dictionary containing the reranked list of documents and a total
        count. The dictionary has 'doc_list' and 'total' keys.

    Raises:
        McpError: If the API call to the retrieval or reranking service fails
                  after all retries.
        ValueError: If an unsupported `scope` value is provided.
    """
    if retriable_codes is None:
        retriable_codes = list(KNOWLEDGE_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)

    async def make_retrieve_request(client, scope):
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    retrieve_url,
                    headers={"Content-Type": "application/json"},
                    json={
                        "repo_id": repo_id,
                        "content": user_query,
                        "page_num": page_num,
                        "page_size": page_size,
                        "filter_string": filter_string,
                        "scope": scope,
                        "extra_repo_ids": extra_repo_ids,
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()["doc_list"]

            except HTTPStatusError as e:
                if (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code in retriable_codes
                    and attempt < max_retries
                ):
                    wait_time = (2**attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Failed to retrieve knowledge base: {str(e)}",
                    )
                ) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Network error: {str(e)}",
                    )
                ) from e

    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        if scope in ("doc", "keyword"):
            doc_list = await make_retrieve_request(client, scope)
        elif scope == "both":
            tasks = [
                make_retrieve_request(client, scope)
                for scope in ["doc", "keyword"]
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            doc_list = []
            for each_result in results:
                if isinstance(each_result, Exception):
                    raise McpError(
                        ErrorData(
                            code=INTERNAL_ERROR,
                            message=f"Retrieval failed: {str(each_result)}",
                        )
                    ) from each_result
                if each_result is not None and isinstance(each_result, list):
                    doc_list.extend(each_result)
        else:
            raise ValueError(
                "Invalid scope value. Must be 'doc', 'keyword', or 'both'."
            )

    if doc_list is None:
        doc_list = []
    elif not isinstance(doc_list, list):
        doc_list = list(doc_list)

    return {
        "doc_list": await rerank(
            user_query=user_query,
            doc_list=doc_list,
            rerank_url=rerank_url,
            top_n=page_size,
            rerank_batch_size=rerank_batch_size,
            score_threshold=score_threshold,
            timeout=timeout,
            retriable_codes=retriable_codes,
            max_retries=max_retries,
        ),
        "total": 10000,
    }


@func_cache(
    key_params=[
        "user_query",
        "retrieve_url",
        "repo_id_dict",
        "page_num",
        "filter_string",
        "scope",
        "extra_repo_ids",
        "rerank_url",
        "rerank_batch_size",
        "score_threshold",
        "top_n",
    ],
    ttl=RETRIEVE_CACHE_TTL,
    exclude_params=["semaphore"],
)
async def multi_retrieve(
    user_query: str,
    retrieve_url: str = KNOWLEDGE_CONFIG.RETRIEVE_URL,
    repo_id_dict: Optional[Dict[str, int]] = None,
    page_num: int = KNOWLEDGE_CONFIG.PAGE_NUM,
    filter_string: Optional[str] = KNOWLEDGE_CONFIG.FILTER_STRING,
    scope: str = KNOWLEDGE_CONFIG.SCOPE,
    extra_repo_ids: Optional[List[str]] = KNOWLEDGE_CONFIG.EXTRA_REPO_IDS,
    rerank_url: str = KNOWLEDGE_CONFIG.RERANK_URL,
    rerank_batch_size: int = KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE,
    score_threshold: float = KNOWLEDGE_CONFIG.SCORE_THRESHOLD,
    top_n: int = KNOWLEDGE_CONFIG.TOP_N,
    timeout: float = KNOWLEDGE_CONFIG.TIMEOUT,
    retriable_codes: Optional[List[int]] = None,
    max_retries: int = KNOWLEDGE_CONFIG.MAX_RETRIES,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> Dict[str, Any]:
    """Concurrently retrieve and rerank documents from multiple repositories.

    This function calls the `retrieve` function for each repository specified
    in `repo_id_dict`. It then merges the results, sorts them by relevance
    score, and returns the top N documents. A semaphore can be used to limit
    the concurrency of the retrieval operations.

    Args:
        user_query: The user's natural language query.
        retrieve_url: The URL of the retrieval service.
        repo_id_dict: A dictionary mapping repository IDs to their page sizes.
        page_num: The page number for pagination of retrieval results.
        filter_string: An optional string for metadata filtering.
        scope: The search scope, which can be 'doc', 'keyword', or 'both'.
        extra_repo_ids: An optional list of additional repository IDs to
                        include in the search.
        rerank_url: The URL of the reranking service.
        rerank_batch_size: The batch size for reranking documents.
        score_threshold: The minimum relevance score to include documents in
                         the final result.
        top_n: The total number of top-scoring documents to return.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.
        semaphore: An optional semaphore to limit concurrency.

    Returns:
        A dictionary containing the merged and sorted list of documents and a
        total count. The dictionary has 'doc_list' and 'total' keys.

    Raises:
        McpError: If any of the underlying `retrieve` operations fail.
    """
    if repo_id_dict is None:
        repo_id_dict = dict(KNOWLEDGE_CONFIG.REPO_ID_DICT)
    else:
        repo_id_dict = dict(repo_id_dict)
    if retriable_codes is None:
        retriable_codes = list(KNOWLEDGE_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)

    async def make_multi_retrieve():
        try:
            tasks = [
                retrieve(
                    user_query=user_query,
                    retrieve_url=retrieve_url,
                    repo_id=repo_id,
                    page_num=page_num,
                    page_size=page_size,
                    filter_string=filter_string,
                    scope=scope,
                    extra_repo_ids=extra_repo_ids,
                    rerank_url=rerank_url,
                    rerank_batch_size=rerank_batch_size,
                    score_threshold=score_threshold,
                    timeout=timeout,
                    retriable_codes=retriable_codes,
                    max_retries=max_retries,
                )
                for repo_id, page_size in repo_id_dict.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            merged_docs = []
            for result in results:
                if isinstance(result, dict) and "doc_list" in result:
                    merged_docs.extend(result["doc_list"])
            sorted_docs = sorted(
                merged_docs, key=lambda x: x["score"], reverse=True
            )
            if top_n is not None and top_n > 0:
                sorted_docs = sorted_docs[:top_n]
            return {
                "doc_list": sorted_docs,
                "total": 10000,
            }
        except (
            ValueError,
            TypeError,
            HTTPStatusError,
            ConnectError,
            TimeoutException,
        ) as e:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Multi-retrieve operation failed: {str(e)}",
                )
            ) from e

    if semaphore is not None:
        async with semaphore:
            return await make_multi_retrieve()
    else:
        return await make_multi_retrieve()


async def rerank(
    user_query: str,
    doc_list: List[Dict[str, Any]],
    rerank_url: str = KNOWLEDGE_CONFIG.RERANK_URL,
    top_n: int = KNOWLEDGE_CONFIG.TOP_N,
    rerank_batch_size: int = KNOWLEDGE_CONFIG.RERANK_BATCH_SIZE,
    score_threshold: float = KNOWLEDGE_CONFIG.SCORE_THRESHOLD,
    timeout: float = KNOWLEDGE_CONFIG.TIMEOUT,
    retriable_codes: Optional[List[int]] = None,
    max_retries: int = KNOWLEDGE_CONFIG.MAX_RETRIES,
) -> list:
    """Rerank a list of documents based on a user query.

    This function sends a list of documents to a reranking service to obtain
    relevance scores. It processes documents in batches and filters the
    results based on a score threshold.

    Args:
        user_query: The user's natural language query.
        doc_list: A list of document dictionaries to be reranked.
        rerank_url: The URL of the reranking service.
        top_n: The number of top-scoring documents to return.
        rerank_batch_size: The batch size for reranking documents.
        score_threshold: The minimum relevance score to include documents in
                         the final result.
        timeout: The timeout for each API call in seconds.
        retriable_codes: A list of HTTP status codes that trigger a retry.
        max_retries: The maximum number of retries for failed requests.

    Returns:
        A list of reranked document dictionaries, sorted by score in
        descending order.

    Raises:
        McpError: If the API call to the reranking service fails after all
                  retries.
    """
    if retriable_codes is None:
        retriable_codes = list(KNOWLEDGE_CONFIG.RETRIABLE_CODES)
    else:
        retriable_codes = list(retriable_codes)

    async def make_rerank_request(client, docs_batch):
        for attempt in range(max_retries + 1):
            try:
                response = await client.post(
                    rerank_url,
                    headers={"Content-Type": "application/json"},
                    json={
                        "query": user_query,
                        "ranking_order": ["title", "content"],
                        "docs": docs_batch,
                        "top_n": top_n,
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.json()["rank_result"]

            except HTTPStatusError as e:
                if (
                    hasattr(e, "response")
                    and e.response is not None
                    and e.response.status_code in retriable_codes
                    and attempt < max_retries
                ):
                    wait_time = (2**attempt) + uniform(0, 1)
                    await asyncio.sleep(wait_time)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Failed to rerank: {str(e)}",
                    )
                ) from e

            except (ConnectError, TimeoutException) as e:
                if attempt < max_retries:
                    await asyncio.sleep(1.5**attempt)
                    continue
                raise McpError(
                    ErrorData(
                        code=INTERNAL_ERROR,
                        message=f"Network error: {str(e)}",
                    )
                ) from e

    docs, id_doc_dict = [], {}
    for doc in doc_list:
        if doc["chunk_id"] not in id_doc_dict:
            if "big_content" in doc:
                docs.append(
                    {
                        "id": doc["chunk_id"],
                        "title": doc["title"],
                        "content": doc["big_content"],
                    }
                )
                id_doc_dict.update({doc["chunk_id"]: doc})
            elif "content" in doc:
                docs.append(
                    {
                        "id": doc["chunk_id"],
                        "title": doc["title"],
                        "content": doc["content"],
                    }
                )
                id_doc_dict.update({doc["chunk_id"]: doc})

    client_timeout = Timeout(timeout, connect=timeout)
    async with AsyncClient(timeout=client_timeout, verify=False) as client:
        if len(docs) > rerank_batch_size:
            chunks = split_list(docs, rerank_batch_size)
            tasks = [make_rerank_request(client, chunk) for chunk in chunks]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            all_results: List[Dict[str, Any]] = []
            for result in results:
                if isinstance(result, BaseException):
                    raise McpError(
                        ErrorData(
                            code=INTERNAL_ERROR,
                            message=f"Reranking failed: {str(result)}",
                        )
                    ) from result
                if isinstance(result, list):
                    all_results.extend(result)
            rank_docs = sorted(
                all_results, key=lambda x: x["score"], reverse=True
            )[:top_n]
        else:
            rank_docs = await make_rerank_request(client, docs)

    if rank_docs is None:
        rank_docs = []
    elif not isinstance(rank_docs, list):
        rank_docs = list(rank_docs)

    return [
        {**id_doc_dict[doc["id"]].copy(), "score": doc["score"]}
        for doc in rank_docs
        if doc["score"] >= score_threshold
    ]


def _knowledge_config_with_overrides(**kwargs: Any):
    """Build a KnowledgeConfig copy from compatibility wrapper arguments."""
    return copy_config_with_overrides(
        KNOWLEDGE_CONFIG,
        kwargs,
        KNOWLEDGE_CONFIG_FIELD_MAP,
    )


def _knowledge_sensitive_config_with_overrides(**kwargs: Any):
    """Build a SensitiveConfig copy from compatibility wrapper arguments."""
    return copy_sensitive_config_with_overrides(
        SENSITIVE_CONFIG,
        kwargs,
        field_map=KNOWLEDGE_SENSITIVE_FIELD_MAP,
        secret_field_map=KNOWLEDGE_SECRET_FIELD_MAP,
    )


async def multi_retrieve_generate(
    user_query: str,
    obs_file_list: Optional[List[str]] = None,
    repo_id_dict: Optional[Dict[str, int]] = None,
    is_generate: bool = True,
    is_follow_up: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper around the LangGraph-based KnowledgeAgent."""
    knowledge_config = _knowledge_config_with_overrides(**kwargs)
    sensitive_config = _knowledge_sensitive_config_with_overrides(**kwargs)
    agent = get_cached_agent(
        "KnowledgeAgent",
        lambda: KnowledgeAgent(
            knowledge_config=knowledge_config,
            sensitive_config=sensitive_config,
        ),
        agent_fingerprint_values(
            knowledge_config=knowledge_config,
            sensitive_config=sensitive_config,
        ),
    )
    return await agent.arun(
        user_query=user_query,
        obs_file_list=obs_file_list or [],
        repo_id_dict=repo_id_dict,
        is_generate=is_generate,
        is_follow_up=is_follow_up,
    )


async def retrieve_generate(
    user_query: str,
    repo_id: str = KNOWLEDGE_CONFIG.REPO_ID,
    page_size: int = KNOWLEDGE_CONFIG.PAGE_SIZE,
    obs_file_list: Optional[List[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Compatibility wrapper for single-repository retrieve and generate."""
    return await multi_retrieve_generate(
        user_query=user_query,
        obs_file_list=obs_file_list or [],
        repo_id_dict={repo_id: page_size},
        **kwargs,
    )


def response_to_string(phyto_response: dict) -> str:
    """Convert a RAG response to a formatted string with references.

    This function extracts the generated content and document list from a
    retrieval-augmented generation response and formats it as a readable
    string with numbered references.

    Args:
        phyto_response: A dictionary containing the response from a RAG
            operation. Expected to have the structure returned by
            `multi_retrieve_generate`, with 'choices' containing message
            content and doc_list.

    Returns:
        A formatted string containing the generated content followed by
        a numbered reference list of the source documents.

    Examples:
        >>> response = {
        ...     'choices': [{
        ...         'message': {
        ...             'content': 'Photosynthesis is...',
        ...             'doc_list': [
        ...                 {'title': 'Plant Biology.pdf'},
        ...                 {'title': 'Botany Research'}
        ...             ]
        ...         }
        ...     }]
        ... }
        >>> result = response_to_string(response)
        >>> print(result)
        Photosynthesis is...

        ## Reference:
        [1] Plant Biology
        [2] Botany Research
    """
    content = ""
    doc_list = []

    if (
        phyto_response
        and "choices" in phyto_response
        and len(phyto_response["choices"]) > 0
    ):
        choice = phyto_response["choices"][0]
        if "message" in choice and choice["message"] is not None:
            message = choice["message"]
            content = message.get("content", "")
            doc_list = message.get("doc_list", [])

    doc_string = ""
    for doc_id, doc in enumerate(doc_list):
        title = doc.get("title", "") if doc is not None else ""
        if title:
            if title[-3:] in ("pdf", "PDF"):
                doc_string += f"[{doc_id+1}] " + title[:-4] + "\n\n"
            else:
                doc_string += f"[{doc_id+1}] " + title + "\n\n"
    return content + "\n\n## Reference:\n\n" + doc_string
